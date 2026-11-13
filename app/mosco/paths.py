"""실행 위치·외부 도구(ffmpeg)·모델 경로. 설치 없이 폴더째 실행."""
import os
import shutil
import sys
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bin_dir() -> Path:
    return app_dir() / "bin"


def model_dir() -> Path:
    return app_dir() / "models"


def asset(name: str) -> Path:
    """그림 파일(assets/…). 빌드 = 프로그램 폴더 assets, 개발 = 저장소 assets."""
    return app_dir() / "assets" / name


def _mk(name) -> Path:
    d = app_dir() / name
    d.mkdir(exist_ok=True)
    return d


def cache_dir() -> Path:
    return _mk("cache")


_USER_DIR = None


def _documents() -> Path:
    """윈도우 '내 문서' 실제 위치(OneDrive 로 옮겨진 경우 포함). 실패하면 홈\\Documents."""
    if os.name == "nt":
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]
            g = GUID()
            ctypes.memmove(ctypes.byref(g), uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7").bytes_le, 16)   # FOLDERID_Documents
            out = ctypes.c_wchar_p()
            sh = ctypes.WinDLL("shell32")
            sh.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                                ctypes.POINTER(ctypes.c_wchar_p)]
            if sh.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0 and out.value:
                p = Path(out.value)
                ctypes.WinDLL("ole32").CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
                return p
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / "Documents"


def user_dir() -> Path:
    """프리셋·설정을 두는 공용 폴더(v0.7.72): 내 문서\\MOSCO — 새 버전을 어느 폴더에 풀어도 그대로 보이게.
    처음 만들 때 프로그램 폴더에 있던 프리셋·settings.json 을 복사해 옴. 못 만들면 프로그램 폴더(예전 방식).
    개발·테스트 실행(exe 아님)은 프로그램 폴더 그대로, 환경변수 MOSCO_USER_DIR 로 바꿀 수 있음."""
    global _USER_DIR
    if _USER_DIR is not None:
        return _USER_DIR
    env = os.environ.get("MOSCO_USER_DIR")
    if env:
        d = Path(env)
    elif getattr(sys, "frozen", False):
        d = _documents() / "MOSCO"
    else:
        _USER_DIR = app_dir()
        return _USER_DIR
    try:
        (d / "presets").mkdir(parents=True, exist_ok=True)
        t = d / ".write_test"
        t.write_text("ok", encoding="utf-8")
        t.unlink()
        _migrate_user(d)
        _USER_DIR = d
    except Exception:  # noqa: BLE001
        _USER_DIR = app_dir()
    return _USER_DIR


def _migrate_user(d: Path):
    """공용 폴더에 없는 것만 프로그램 폴더에서 복사(덮어쓰지 않음)."""
    old = app_dir()
    if old.resolve() == d.resolve():
        return
    try:
        if not any((d / "presets").glob("*.json")) and (old / "presets").is_dir():
            for f in (old / "presets").glob("*.json"):
                shutil.copy2(f, d / "presets" / f.name)
        if not (d / "settings.json").exists() and (old / "settings.json").exists():
            shutil.copy2(old / "settings.json", d / "settings.json")
    except Exception:  # noqa: BLE001
        pass


def preset_dir() -> Path:
    d = user_dir() / "presets"
    d.mkdir(exist_ok=True)
    return d


def log_dir() -> Path:
    d = user_dir() / "logs"          # v0.7.82 프로그램 폴더 대신 내 문서(업데이트해도 유지·찾기 쉬움)
    d.mkdir(exist_ok=True)
    return d


def settings_path() -> Path:
    return user_dir() / "settings.json"


def _tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    local = bin_dir() / exe
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(f"{exe} 를 찾을 수 없습니다. bin 폴더에 넣어주세요.")


def ffmpeg() -> str:
    return _tool("ffmpeg")


def ffprobe() -> str:
    return _tool("ffprobe")
