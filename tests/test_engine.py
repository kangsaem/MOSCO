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


def make_sign_clip(out: Path, n=30):
    """간판(영문·숫자, cv2 글꼴) 3개가 원근으로 비스듬히 보이는 화면 + 천천히 패닝. ProRes 422 HQ 29.97."""
    rng = np.random.default_rng(5)
    bg = cv2.GaussianBlur((rng.random((1080, 1920, 3)) * 255).astype(np.uint8), (0, 0), 25)
    boards = [("OPEN 24H", (40, 40, 40), (0, 220, 255), [[200, 150], [780, 210], [770, 360], [190, 330]]),
              ("CAFE 1234", (30, 30, 200), (255, 255, 255), [[1150, 620], [1700, 580], [1720, 760], [1160, 800]]),
              ("010-555-0101", (250, 250, 250), (20, 20, 20), [[300, 780], [800, 800], [798, 880], [298, 860]])]
    for t, bgc, fgc, quad in boards:
        b = np.full((140, 620, 3), bgc, np.uint8)
        cv2.putText(b, t, (24, 100), cv2.FONT_HERSHEY_DUPLEX, 2.2, fgc, 5, cv2.LINE_AA)
        M = cv2.getPerspectiveTransform(np.float32([[0, 0], [620, 0], [620, 140], [0, 140]]), np.float32(quad))
        w = cv2.warpPerspective(b, M, (1920, 1080))
        m = cv2.warpPerspective(np.ones((140, 620), np.float32), M, (1920, 1080))[..., None]
        bg = (bg * (1 - m) + w * m).astype(np.uint8)
    p = subprocess.Popen([paths.ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1920x1080",
                          "-r", "30000/1001", "-i", "-", "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
                          str(out)], stdin=subprocess.PIPE)
    for i in range(n):
        M = np.float32([[1, 0, -i * 1.5], [0, 1, 0]])
        p.stdin.write(cv2.warpAffine(bg, M, (1920, 1080), borderMode=cv2.BORDER_REFLECT).tobytes())
    p.stdin.close()
    p.wait()
    return boards


def sign_check(work: Path):
    """v0.7.8 간판 블러: 글자 검출 → 4꼭짓점 트랙 → 렌더(본편·4444). 사각형 알파 정확도."""
    from mosco import edits, sign
    from mosco.render import make_track
    clip = work / "signs.mov"
    boards = make_sign_clip(clip)
    info = ff.probe(str(clip))
    t = time.time()
    z = analyze.analyze(info, "sign")
    s = Source(str(clip), "sign")
    tr = make_track(s, z)
    assert tr.ids >= 3, f"간판 트랙 {tr.ids}"
    items = edits.Resolver(s, tr).items(15)
    assert len(items) >= 3 and all(it.pts is not None and np.asarray(it.pts).size == 8 for it in items)
    for _, _, _, quad in boards:              # 간판마다 그 판 중심을 덮는 사각형이 있어야 함(패닝 15f = -22px)
        c = np.mean(quad, 0) - (22, 0)
        assert any(cv2.pointPolygonTest(np.asarray(it.pts, np.float32), (float(c[0]), float(c[1])), False) >= 0
                   for it in items), f"간판 {c} 못 덮음"
    outs = render(s, dict(RENDER_DEFAULT), str(work))
    assert ff.probe(outs["main"]).frames == info.frames
    v2 = [x for x in probe_streams(outs["overlay"])["streams"] if x["codec_type"] == "video"][0]
    assert "yuva444" in v2["pix_fmt"], v2["pix_fmt"]
    r = subprocess.run([paths.ffmpeg(), "-v", "error", "-i", outs["overlay"], "-vf", r"select=eq(n\,15),alphaextract",
                        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    al = np.frombuffer(r.stdout, np.uint8).reshape(1080, 1920)
    c = np.mean(boards[1][3], 0) - (22, 0)
    assert al[int(c[1]), int(c[0])] > 230 and al[540, 960] < 5, "4444 알파: 간판 = 불투명, 바깥 = 투명이어야 함"
    # 사각형 알파(축소 계산) 대 전체 해상도
    q = np.float32([[800, 400], [1150, 430], [1140, 560], [810, 520]])
    m = np.zeros((1080, 1920), np.float32)
    cv2.fillPoly(m, [np.round(q * 16).astype(np.int32)], 1.0, cv2.LINE_AA, 4)
    d = float(np.abs(sign.quad_alpha(q, (600, 200, 1350, 760), 40) - cv2.GaussianBlur(m, (0, 0), 40)[200:760, 600:1350]).max())
    assert d < 0.02, f"사각형 알파 차이 {d}"
    print(f"sign OK  tracks={tr.ids} {time.time() - t:.1f}s alpha {d:.4f}")
    # v0.7.31 얼굴+간판 동시 블러: 한 소스가 두 종류를 함께(순차 분석 → 이어서 블러)
    from mosco.render import build_plan, process
    sb = Source(str(clip), "blur")
    sb.both = True
    assert sb.blur_kinds() == ["blur", "sign"], sb.blur_kinds()
    plan, n, _, _ = build_plan(sb, info, dict(RENDER_DEFAULT))
    assert plan[0] == "blur" and len(plan[1]) == 2, "두 종류 plan 아님"
    o, a, ells = process(sb, plan, ff.grab(info, 15, 16), 15, True)
    nsign = sum(1 for it, _ in ells if getattr(it, "pts", None) is not None and np.asarray(it.pts).size == 8)
    assert nsign >= 3, f"동시 블러에 간판 {nsign}"      # 합성 간판 3개
    outs2 = render(sb, dict(RENDER_DEFAULT), str(work))
    assert ff.probe(outs2["main"]).frames == info.frames
    print(f"both OK  동시 블러 간판 {nsign}")
    # v0.7.53 샘플: 간판 모드는 간판 설정(src.sign)으로 블러해야 함(예전엔 얼굴블러 설정 src.blur 를 씀)
    from mosco import sample as smp
    outs_s = {}
    for kind in ("blur", "mosaic"):
        ss = Source(str(clip), "sign")
        ss.sign["kind"] = kind                 # 얼굴블러 설정(ss.blur)은 기본 그대로
        d = work / f"smp_{kind}"
        p = smp.render_samples(ss, info, {"out_dir": str(d), "count": 1, "fmt": "png8"})
        outs_s[kind] = cv2.imread(p[0])
    assert np.abs(outs_s["blur"].astype(int) - outs_s["mosaic"].astype(int)).max() > 20, "샘플이 간판 설정을 안 씀"
    sb2 = Source(str(clip), "blur")
    sb2.both = True
    p = smp.render_samples(sb2, info, {"out_dir": str(work / "smp_both"), "count": 1, "fmt": "png8"})
    orig = cv2.cvtColor((ff.grab(info, 15, 16) >> 8).astype(np.uint8), cv2.COLOR_RGB2BGR)
    c = np.mean(boards[1][3], 0) - (22, 0)
    x, y = int(c[0]), int(c[1])
    diff = np.abs(cv2.imread(p[0]).astype(int) - orig.astype(int))[y - 10:y + 10, x - 10:x + 10].mean()
    assert diff > 3, f"동시 블러 샘플에 간판 블러 없음({diff:.1f})"
    print(f"sample OK  간판 설정 반영 · 동시 블러 간판 차이 {diff:.1f}")


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
    if mode in ("blur", "sign"):
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
    sign_check(work)
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
    # v0.7.54 큰 블러 축소 계산(fx._gauss): 그냥 가우시안과 1% 이내 · 작은 시그마는 그대로
    big = cv2.GaussianBlur(rng.random((700, 560, 3)).astype(np.float32), (0, 0), 3)
    big[200:500, 150:400] += 0.15
    dg = max(float(np.abs(fx._gauss(big, sg) - cv2.GaussianBlur(big, (0, 0), sg)).max()) for sg in (9, 25, 50))
    assert dg < 0.01, f"큰 블러 축소 계산 차이 큼 {dg}"
    assert np.array_equal(fx._gauss(big, 5), cv2.GaussianBlur(big, (0, 0), 5))
    # v0.7.54 프레임 캐시: 용량 상한 · 안 보는 소스는 조금만
    from mosco import decoder as dec
    info = ff.probe(str(syn))
    fs = dec.source(str(syn))
    if fs is not None:
        old = dec.CACHE_BYTES
        dec.CACHE_BYTES = info.w * info.h * 6 * 10            # 10장 분량
        for k in range(20):
            fs.frame(k)
        assert len(fs.cache) <= 10, len(fs.cache)
        dec.CACHE_BYTES = old
        dec.focus("다른 소스")
        assert len(fs.cache) <= dec.IDLE_FRAMES
    # v0.7.54 인코더가 먼저 죽으면 멈추지 않고 이유를 알림
    wbad = ff.Writer(str(work / "없는폴더" / "x.mov"), info, audio=False)
    try:
        for _ in range(200):
            wbad.write(np.zeros((info.h, info.w, 3), np.uint16))
        wbad.close()
        raise AssertionError("없는 폴더인데 오류가 안 남")
    except RuntimeError as e:
        assert str(e), "오류 내용 없음"
    finally:
        wbad.kill()
    print(f"fast blur OK  alpha {worst:.4f} low {d:.4f} gauss {dg:.4f} · cache · writer error")
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
