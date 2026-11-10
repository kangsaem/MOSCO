"""FFmpeg: 정보 읽기, 프레임 읽기(파이프), ProRes 쓰기, 타임코드."""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import tempfile
import threading
import weakref
from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np

from . import paths

# CREATE_NO_WINDOW — ffmpeg 를 창 없이 띄움. v0.7.26: 우선순위 '보통'(BELOW_NORMAL 제거)
# → 다른 작업 중에도 MOSCO 진행이 멈추지 않게(예전 '보통 이하'는 양보가 과해 진행이 안 됐음)
NOWIN = 0x08000000 if os.name == "nt" else 0


# 살아있는 ffmpeg 자식 프로세스 등록부 → 프로그램 종료 시 모두 강제 종료
# (안 하면 분석·재생용 ffmpeg 가 창을 닫아도 남아서 CPU/GPU 를 계속 씀)
_procs: "weakref.WeakSet" = weakref.WeakSet()
_procs_lock = threading.Lock()


# ------------------------------------------------------------ 윈도우 Job Object
# 정상 종료(창 닫기·atexit)는 아래 kill_all 로 처리되지만, 크래시·강제 종료(작업 관리자
# "작업 끝내기", 전원 차단 외 프로세스 kill)에는 atexit 가 돌지 않아 ffmpeg 가 고아로 남는다.
# → MOSCO 를 Job Object 에 넣고 KILL_ON_JOB_CLOSE 를 주면, MOSCO 프로세스가 어떻게 끝나든
#   OS 가 Job 핸들을 닫으면서 그 안의 자식 ffmpeg 를 전부 정리한다. (실패하면 조용히 폴백)
_job = None          # Job 핸들(살아 있어야 함 → 모듈 전역으로 유지)
_job_ready = False    # True 면 생성 시도 완료(성공/실패 무관)


def _win_job():
    global _job, _job_ready
    if _job_ready:
        return _job
    _job_ready = True
    # 기본 꺼짐: CI(윈도우 러너)·일부 환경에서 러너의 기존 Job 과 충돌해 자식이 죽는 사례가 있어
    # 실기 검증 전까지 옵트인(MOSCO_JOB=1). 정상 종료 정리는 WeakSet+atexit(kill_all)로 충분.
    if os.environ.get("MOSCO_JOB") != "1":
        return None
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in
                        ("Read", "Write", "Other", "ReadT", "WriteT", "OtherT")]

        class _EXT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                wintypes.LPVOID, wintypes.DWORD]

        h = k32.CreateJobObjectW(None, None)
        if not h:
            return None
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(h)
            return None
        _job = (k32, h)
        return _job
    except Exception:  # noqa: BLE001
        return None


def _assign_job(p):
    """자식 프로세스를 Job 에 편입(윈도우). 실패해도 WeakSet 폴백이 있으므로 조용히 무시."""
    job = _win_job()
    if not job or p.pid is None:
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32, h = job
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        hp = k32.OpenProcess(0x0001 | 0x0100, False, p.pid)   # TERMINATE | SET_QUOTA
        if hp:
            k32.AssignProcessToJobObject(h, hp)
            k32.CloseHandle(hp)
    except Exception:  # noqa: BLE001
        pass


def _track(p):
    with _procs_lock:
        _procs.add(p)
    _assign_job(p)
    return p


def kill_all():
    """남아있는 ffmpeg 프로세스를 모두 종료. 창을 닫을 때·프로그램 끝날 때 호출."""
    with _procs_lock:
        procs = list(_procs)
    for p in procs:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:  # noqa: BLE001
            pass
    for p in procs:
        try:
            p.wait(1)
        except Exception:  # noqa: BLE001
            pass


atexit.register(kill_all)


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, creationflags=NOWIN, **kw)


@dataclass
class Info:
    path: str
    w: int
    h: int
    fps: Fraction
    frames: int
    duration: float
    timecode: str = ""
    matrix: str = "bt709"
    has_audio: bool = False
    audio_ch: int = 0
    pix_fmt: str = ""
    codec: str = ""
    field_order: str = "progressive"     # tt / bb / tb / bt = 인터레이스
    extra: dict = field(default_factory=dict)

    @property
    def fps_f(self) -> float:
        return float(self.fps)


def probe(path: str) -> Info:
    r = _run([paths.ffprobe(), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path])
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or "ffprobe 실패")
    j = json.loads(r.stdout)
    vs = [s for s in j["streams"] if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")]
    if not vs:
        raise RuntimeError("영상 스트림이 없습니다")
    v = vs[0]
    aus = [s for s in j["streams"] if s["codec_type"] == "audio"]
    fps = Fraction(v.get("avg_frame_rate") or v.get("r_frame_rate") or "30000/1001")
    if fps == 0:
        fps = Fraction(v.get("r_frame_rate", "30000/1001"))
    dur = float(v.get("duration") or j["format"].get("duration") or 0)
    nb = int(v.get("nb_frames") or 0) or int(round(dur * float(fps)))
    tc = v.get("tags", {}).get("timecode") or j["format"].get("tags", {}).get("timecode", "")
    for s in j["streams"]:
        tc = tc or s.get("tags", {}).get("timecode", "")
    cs = v.get("color_space", "")
    matrix = "bt601" if cs in ("smpte170m", "bt470bg") else ("bt2020" if cs.startswith("bt2020") else "bt709")
    if not cs and int(v["height"]) < 720:
        matrix = "bt601"
    return Info(path, int(v["width"]), int(v["height"]), fps, nb, dur, tc, matrix,
                bool(aus), int(aus[0].get("channels", 2)) if aus else 0, v.get("pix_fmt", ""), v.get("codec_name", ""),
                v.get("field_order", "progressive") or "progressive")


def _read_err(f) -> str:
    """stderr 임시파일 내용(끝 4000자)."""
    try:
        f.seek(0)
        return f.read().decode("utf-8", "replace").strip()[-4000:]
    except Exception:  # noqa: BLE001
        return ""


def _ss(frame: int, fps: Fraction) -> str:
    """n번 프레임 정확 탐색: (n-0.5)/fps (DTME 실측)."""
    return f"{max(0.0, (frame - 0.5) / float(fps)):.6f}"


class Reader:
    """원본 프레임을 순서대로 읽는 파이프.
    depth=16 → uint16 RGB (렌더용, 10bit 보존) / depth=8 → uint8 RGB (분석·재생)."""

    def __init__(self, info: Info, start=0, depth=8, size=None, hw=False, count=None):
        self._args = (info, start, depth, size, False, count)
        self.hw = hw
        self._first = True
        self.info = info
        self.w, self.h = size or (info.w, info.h)
        self.depth = depth
        fmt = "rgb48le" if depth == 16 else "rgb24"
        vf = f"scale={self.w}:{self.h}:in_color_matrix={info.matrix}:in_range=auto:flags=accurate_rnd+full_chroma_int+bicubic,format={fmt}"
        args = [paths.ffmpeg(), "-v", "error", "-nostdin"]
        if hw:
            args += ["-hwaccel", "cuda"]
        if start > 0:
            args += ["-ss", _ss(start, info.fps)]
        args += ["-i", info.path, "-map", "0:v:0", "-an", "-sn", "-vf", vf]
        if count:
            args += ["-frames:v", str(count)]
        args += ["-f", "rawvideo", "-pix_fmt", fmt, "-"]
        self.nbytes = self.w * self.h * 3 * (2 if depth == 16 else 1)
        self._err = tempfile.TemporaryFile()      # stderr 는 파일로(파이프가 가득 차 ffmpeg 가 멈추는 일 방지)
        self.p = _track(subprocess.Popen(args, stdout=subprocess.PIPE, stderr=self._err, creationflags=NOWIN,
                                         bufsize=self.nbytes * 2))

    def read(self):
        buf = self.p.stdout.read(self.nbytes)
        if len(buf) < self.nbytes and self._first and self.hw:
            # GPU 디코딩 실패 → CPU 로 다시
            self.close()
            self.__init__(*self._args)
            return self.read()
        self._first = False
        if len(buf) < self.nbytes:
            return None
        a = np.frombuffer(buf, np.uint16 if self.depth == 16 else np.uint8)
        return a.reshape(self.h, self.w, 3)

    def error(self) -> str:
        try:
            self.p.wait(3)
        except Exception:  # noqa: BLE001
            pass
        return _read_err(self._err)

    def close(self):
        try:
            self.p.stdout.close()
            self.p.kill()
            self.p.wait(3)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._err.close()
        except Exception:  # noqa: BLE001
            pass


def grab(info: Info, frame: int, depth=16, size=None):
    """프레임 1장."""
    r = Reader(info, frame, depth, size, count=1)
    try:
        a = r.read()
        if a is None:
            raise RuntimeError(r.error() or "프레임을 읽지 못했습니다")
        return a.copy()
    finally:
        r.close()


PRORES = {"422 HQ": 3, "422": 2, "422 LT": 1}


class Writer:
    """uint16 RGB(또는 RGBA) 프레임 → ProRes .mov. 오디오는 원본에서 그대로(PCM)."""

    def __init__(self, out: str, info: Info, kind="422 HQ", alpha=False, audio=True, h264=False):
        self.alpha = alpha
        self.depth8 = h264
        fmt = "rgba64le" if alpha else ("rgb24" if h264 else "rgb48le")
        args = [paths.ffmpeg(), "-v", "error", "-y", "-nostdin",
                "-f", "rawvideo", "-pix_fmt", fmt, "-s", f"{info.w}x{info.h}", "-framerate", str(info.fps), "-i", "-"]
        use_audio = audio and info.has_audio
        if use_audio:
            # 오디오는 영상 길이만큼(마지막 프레임이 잘리지 않게 -shortest 대신)
            t = info.frames / float(info.fps) if info.frames else info.duration
            args += ["-t", f"{t:.6f}", "-i", info.path, "-map", "0:v", "-map", "1:a?"]
        m = info.matrix
        if h264:
            vf = f"scale=out_color_matrix={m}:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p"
            args += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
        elif alpha:
            vf = f"scale=out_color_matrix={m}:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuva444p10le"
            args += ["-vf", vf, "-c:v", "prores_ks", "-profile:v", "4", "-alpha_bits", "16",
                     "-vendor", "apl0", "-pix_fmt", "yuva444p10le"]
        else:
            vf = f"scale=out_color_matrix={m}:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv422p10le"
            args += ["-vf", vf, "-c:v", "prores_ks", "-profile:v", str(PRORES.get(kind, 3)),
                     "-vendor", "apl0", "-pix_fmt", "yuv422p10le"]
        if info.field_order in ("tt", "bb", "tb", "bt") and not h264:
            # 인터레이스 원본: 필드 순서 유지(프레임 단위 처리, 4:2:2 라 색 번짐 없음)
            args += ["-flags", "+ildct+ilme", "-field_order", info.field_order]
        prim = {"bt709": "bt709", "bt601": "smpte170m", "bt2020": "bt2020"}[m]
        args += ["-colorspace", {"bt709": "bt709", "bt601": "smpte170m", "bt2020": "bt2020nc"}[m],
                 "-color_primaries", {"bt709": "bt709", "bt601": "smpte170m", "bt2020": "bt2020"}[prim],
                 "-color_trc", {"bt709": "bt709", "bt601": "smpte170m", "bt2020": "bt2020-10"}[m],
                 "-color_range", "tv"]
        if use_audio:
            args += ["-c:a", "aac", "-b:a", "320k"] if h264 else ["-c:a", "pcm_s24le"]
        if info.timecode:
            args += ["-timecode", info.timecode]
        args += [out]
        self.out = out
        self._err = tempfile.TemporaryFile()      # stderr 는 파일로(파이프가 가득 차 ffmpeg 가 멈추는 일 방지)
        self.p = _track(subprocess.Popen(args, stdin=subprocess.PIPE, stderr=self._err, creationflags=NOWIN))

    def write(self, frame: np.ndarray):
        if self.depth8:
            if frame.dtype != np.uint8:
                frame = (frame >> 8).astype(np.uint8)
            a = np.ascontiguousarray(frame)
        else:
            a = np.ascontiguousarray(frame, np.uint16)
        try:
            self.p.stdin.write(memoryview(a).cast("B"))      # 복사 없이 그대로 보냄
        except (BrokenPipeError, OSError) as e:
            # 인코더가 먼저 죽음(디스크 가득·경로 문제 등) → ffmpeg 가 남긴 이유를 그대로 알림
            try:
                self.p.wait(3)
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(_read_err(self._err) or f"인코더가 중단되었습니다({e})") from e

    def close(self) -> str:
        try:
            self.p.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        code = self.p.wait()
        err = _read_err(self._err)
        self._close_err()
        if code:
            raise RuntimeError(err or f"ffmpeg 오류 {code}")
        return err

    def _close_err(self):
        try:
            self._err.close()
        except Exception:  # noqa: BLE001
            pass

    def kill(self):
        try:
            self.p.kill()
        except Exception:  # noqa: BLE001
            pass
        self._close_err()


# ------------------------------------------------------------ 타임코드
def _drop(fps: Fraction) -> bool:
    return abs(float(fps) - 29.97) < 0.01 or abs(float(fps) - 59.94) < 0.01


def tc_to_frames(tc: str, fps: Fraction) -> int:
    if not tc:
        return 0
    try:
        sep = ";" in tc or "." in tc
        hh, mm, ss, ff = [int(x) for x in tc.replace(";", ":").replace(".", ":").split(":")]
    except ValueError:
        return 0
    nom = round(float(fps))
    n = ((hh * 60 + mm) * 60 + ss) * nom + ff
    if sep and _drop(fps):
        d = 2 if nom == 30 else 4
        tm = hh * 60 + mm
        n -= d * (tm - tm // 10)
    return n


def frames_to_tc(n: int, fps: Fraction) -> str:
    nom = round(float(fps))
    if _drop(fps):
        d = 2 if nom == 30 else 4
        per10 = nom * 600 - d * 9
        per1 = nom * 60 - d
        tens, rem = divmod(n, per10)
        if rem > d:
            n += d * 9 * tens + d * ((rem - d) // per1)
        else:
            n += d * 9 * tens
        sep = ";"
    else:
        sep = ":"
    ff = n % nom; s = n // nom
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}{sep}{ff:02d}"
