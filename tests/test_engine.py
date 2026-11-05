"""엔진 점검(CI·로컬 공용). 합성 FHD 29.97 ProRes(타임코드·오디오) → 분석 → 렌더 → 출력 검사.

사용: python tests/test_engine.py [작업폴더] [실제영상 ...]
- 합성 영상(얼굴 없음): 출력 프레임 수·코덱·타임코드·오디오·무손실 통과(차이 0) 확인
- 실제 영상(선택): 분석·렌더가 끝까지 되는지 + 걸린 시간
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import numpy as np  # noqa: E402

from mosco import analyze, ff, models, paths  # noqa: E402
from mosco.project import RENDER_DEFAULT, Source  # noqa: E402
from mosco.render import render  # noqa: E402


def probe_streams(f):
    import json
    r = subprocess.run([paths.ffprobe(), "-v", "error", "-show_streams", "-show_format", "-of", "json", f],
                       capture_output=True)
    return json.loads(r.stdout)


def make_synth(out: Path, n=60):
    subprocess.run([paths.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s=1920x1080:r=30000/1001:d={n / 29.97:.4f}", "-f", "lavfi", "-i",
                    "sine=frequency=1000:sample_rate=48000", "-t", f"{n * 1001 / 30000:.6f}",
                    "-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le",
                    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-c:a", "pcm_s24le", "-timecode", "10:00:00;00", str(out)], check=True)


def check_output(src, outs, mode):
    si = ff.probe(src)
    j = probe_streams(outs["main"])
    v = [s for s in j["streams"] if s["codec_type"] == "video"][0]
    assert v["codec_name"] == "prores" and v["pix_fmt"] == "yuv422p10le", v
    assert v.get("profile") == "HQ", v.get("profile")
    oi = ff.probe(outs["main"])
    assert oi.frames == si.frames, (oi.frames, si.frames)
    assert oi.timecode == si.timecode, (oi.timecode, si.timecode)
    assert any(s["codec_type"] == "audio" and s["codec_name"] == "pcm_s24le" for s in j["streams"]), "오디오 없음"
    if mode == "blur":
        j2 = probe_streams(outs["overlay"])
        v2 = [s for s in j2["streams"] if s["codec_type"] == "video"][0]
        assert v2["codec_name"] == "prores" and "yuva444" in v2["pix_fmt"], v2["pix_fmt"]
    return oi


def main():
    work = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].lower().endswith((".mov", ".mp4", ".mxf"))
                else "engine_test")
    work.mkdir(exist_ok=True)
    reals = [a for a in sys.argv[1:] if a.lower().endswith((".mov", ".mp4", ".mxf"))]
    # 모델
    d = models.detector()
    m = models.mesh()
    z = np.zeros((1080, 1920, 3), np.uint8)
    assert d.detect(z, 0.5, tiles=True) == [], "빈 화면에서 얼굴 검출됨"
    pts, conf = m.run(z, (960, 540), 400, 0.0)
    assert pts.shape == (468, 2)
    print("models OK")
    # 타임코드 계산
    from fractions import Fraction
    f = Fraction(30000, 1001)
    for tc in ("00:00:00;00", "00:01:00;02", "00:10:00;00", "10:00:00;00", "23:59:59;29"):
        assert ff.frames_to_tc(ff.tc_to_frames(tc, f), f) == tc, tc
    print("timecode OK")
    # 합성 영상
    syn = work / "synth.mov"
    make_synth(syn)
    for mode in ("beauty", "blur"):
        s = Source(str(syn), mode)
        rs = dict(RENDER_DEFAULT, review=True)
        t = time.time()
        outs = render(s, rs, str(work))
        oi = check_output(str(syn), outs, mode)
        a = ff.grab(ff.probe(str(syn)), 30, 16).astype(np.int32)
        b = ff.grab(oi, 30, 16).astype(np.int32)
        diff = float(np.abs(a - b).mean())
        # 얼굴이 없으면 처리 없음 → ProRes 재인코딩 오차만(합성 화면의 날카로운 경계에서 생김)
        assert diff < 200, f"{mode}: 원본과 차이가 큼 {diff}"
        print(f"{mode} OK  {time.time() - t:.1f}s  meandiff16={diff:.1f}  {list(outs.values())}")
    for rpath in reals:
        info = ff.probe(rpath)
        for mode in ("beauty", "blur"):
            t = time.time()
            zz = analyze.analyze(info, mode)
            ta = time.time() - t
            s = Source(rpath, mode)
            t = time.time()
            outs = render(s, dict(RENDER_DEFAULT, review=True), str(work))
            check_output(rpath, outs, mode)
            print(f"real {Path(rpath).name} {mode}: frames={int(zz['n'])} analyze {ta:.1f}s render {time.time() - t:.1f}s")
    print("ENGINE OK")


if __name__ == "__main__":
    main()
