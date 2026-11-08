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

import cv2
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
    # v0.7.6 렌더 속도: 큰 페더·주름 저주파를 축소 계산 — 원래(전체 해상도)와 차이가 거의 없어야 함
    from mosco import fx
    rng = np.random.default_rng(3)
    worst = 0.0
    for _ in range(12):
        ew, eh = rng.uniform(150, 700), rng.uniform(200, 900)
        ell = (rng.uniform(300, 1600), rng.uniform(200, 900), ew, eh, rng.uniform(-30, 30))
        fp = max(ew, eh) * rng.uniform(0.05, 0.3) / 2
        roi = fx.ellipse_roi(ell, fp, 1920, 1080)
        if roi is None:
            continue
        # 기준 = 정확한 타원(수식, 화소당 2×2 점) 전체 해상도 + 화면 밖까지 이어진 채로 블러 → 화면 안만.
        # (예전 cv2.ellipse 는 면적 ~0.4% 크고 다각형 오차 → 기준으로 안 씀)
        cx, cy = ell[0], ell[1]
        r = max(ew, eh) / 2 + fp * 4 + 4
        ux0, uy0 = int(cx - r), int(cy - r)
        yy, xx = np.mgrid[0:int(2 * r) + 2, 0:int(2 * r) + 2].astype(np.float32)
        ref = np.zeros(xx.shape, np.float32)
        a_ = np.deg2rad(ell[4])
        for oy in (-0.25, 0.25):
            for ox in (-0.25, 0.25):
                X, Y = xx + ox + ux0 - cx, yy + oy + uy0 - cy
                u = (X * np.cos(a_) + Y * np.sin(a_)) / (ew / 2)
                v = (-X * np.sin(a_) + Y * np.cos(a_)) / (eh / 2)
                ref += ((u * u + v * v) <= 1) * 0.25
        ref = cv2.GaussianBlur(ref, (0, 0), fp)[roi[1] - uy0:roi[3] - uy0, roi[0] - ux0:roi[2] - ux0]
        worst = max(worst, float(np.abs(fx.ellipse_alpha(ell, roi, fp) - ref).max()))
    assert worst < 0.01, f"페더 축소 계산 차이 큼 {worst}"          # 알파 1% 이내
    img = cv2.GaussianBlur(rng.random((600, 640, 3)).astype(np.float32), (0, 0), 3)
    d = float(np.abs(fx.low_pass(img, 14) - cv2.GaussianBlur(img, (0, 0), 14)).max())
    assert d < 0.008, f"저주파 축소 계산 차이 큼 {d}"      # 잡음 영상 기준(실제 얼굴 결과 차이는 8bit 1단계 미만)
    print(f"fast blur OK  alpha {worst:.4f} low {d:.4f}")
    # v0.7.4 분석 하한(블러): 하한별 캐시 · 낮은 하한 캐시는 높은 요구에도 씀 · 같은 분석 동시 요청 = 한 번만
    import threading
    info = ff.probe(str(syn))
    import shutil
    from mosco import paths
    shutil.rmtree(paths.cache_dir() / "analysis", ignore_errors=True)
    assert analyze.level_for("blur", {"min_size": 12}) == 12 and analyze.level_for("blur", {"min_size": 30}) == 25
    assert analyze.level_for("beauty", {"min_size": 30}) == 0
    calls = []
    orig = analyze.analyze_blur

    def counted(*a, **k):
        calls.append(k.get("small_min"))
        return orig(*a, **k)
    analyze.analyze_blur = counted
    t = time.time()
    ths = [threading.Thread(target=analyze.analyze, args=(info, "blur"), kwargs={"level": 25}) for _ in range(2)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    analyze.analyze_blur = orig
    assert calls == [25], f"같은 분석이 중복 실행됨: {calls}"
    assert analyze.cache_file(info, "blur", 25).exists()
    assert analyze.load(info, "blur", 30) is not None          # 하한 25 분석 = 최소 크기 30 에 충분
    assert analyze.load(info, "blur", 12) is None              # 더 작은 얼굴은 못 찾았으니 다시 분석해야 함
    print(f"analysis level OK  {time.time() - t:.1f}s")
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
