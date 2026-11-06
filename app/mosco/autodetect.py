"""AUTO: 클립 몇 장을 보고 장면 속성(작업·장소·인원·각도·카메라)을 추정하고, 속성 → 보정값으로 바꾼다.

전체 분석이 아니라 고르게 뽑은 10여 장만 본다(가볍다). 결과는 '출발점'일 뿐 — 사용자가 고치거나 렌더에서 다듬는다.
"""
from __future__ import annotations

import numpy as np

from . import decoder, ff, models
from .project import BEAUTY_DEFAULT, BLUR_DEFAULT

# 속성 후보(순서 = 화면 버튼 순서, 맨 앞은 '자동' 자리라 뺌)
WORK = ["beauty", "blur"]
PLACE = ["market", "street", "indoor", "event"]
PEOPLE = ["one", "few", "crowd"]
ANGLE = ["front", "side", "back"]
CAMERA = ["fixed", "moving", "handheld"]

LABEL = {
    "beauty": "뷰티", "blur": "블러",
    "market": "시장", "street": "거리", "indoor": "실내", "event": "행사장",
    "one": "1인", "few": "2~5명", "crowd": "군중",
    "front": "정면", "side": "측면", "back": "뒷모습",
    "fixed": "고정", "moving": "무빙", "handheld": "핸드헬드",
}
ROWS = [("work", "작업", WORK), ("place", "장소", PLACE), ("people", "인원", PEOPLE),
        ("angle", "각도", ANGLE), ("camera", "카메라", CAMERA)]


def default_scene():
    return {"work": "auto", "place": "auto", "people": "auto", "angle": "auto", "camera": "auto"}


def scene_label(scene, detected=None):
    """'블러 · 시장 · 군중' 식 표시. auto 는 detected 값으로 채움(없으면 '자동')."""
    detected = detected or {}
    parts = []
    for key, _, _ in ROWS:
        v = scene.get(key, "auto")
        if v == "auto":
            v = detected.get(key)
        if v:
            parts.append(LABEL.get(v, v))
    return " · ".join(parts) if parts else "자동"


# ------------------------------------------------------------ 인식
def _sample_indices(n, k=12):
    if n <= k:
        return list(range(n))
    return [int(round(i * (n - 1) / (k - 1))) for i in range(k)]


def detect_scene(info: ff.Info, cancel=None, progress=None):
    """고르게 뽑은 프레임으로 속성 추정. 반환: detected dict + 간단한 통계."""
    idxs = _sample_indices(info.frames, 12)
    face_counts, person_counts = [], []
    face_frac, side_frac, backish = [], [], []
    prev = None
    motion = []
    for j, i in enumerate(idxs):
        if cancel is not None and cancel.is_set():
            from .analyze import Cancelled
            raise Cancelled()
        fr = decoder.grab(info, i, 8, (960, 540))
        faces = models.detector().detect(fr, 0.5, tiles=True, min_size=10)
        persons = models.person_detector().detect(fr, 0.4)
        face_counts.append(len(faces))
        person_counts.append(max(len(faces), len(persons)))
        # 각도: 얼굴이 사람 수만큼 잡히면 정면, 얼굴 적고 몸만 많으면 뒷모습, 중간이면 측면
        np_ = max(len(persons), len(faces), 1)
        face_frac.append(len(faces) / np_)
        # 측면 추정: 얼굴 키포인트(두 눈) 좌우 비대칭
        s = 0
        for d in faces:
            e0, e1, nose = d.kps[0], d.kps[1], d.kps[2]
            eye_mid = (e0 + e1) / 2
            span = max(1.0, abs(e1[0] - e0[0]))
            if abs(nose[0] - eye_mid[0]) / span > 0.5:
                s += 1
        side_frac.append(s / max(1, len(faces)))
        backish.append(1.0 if (len(persons) > len(faces) and len(persons) > 0) else 0.0)
        # 카메라 움직임: 다운스케일 프레임 차
        small = fr[::8, ::8].astype(np.float32).mean(-1)
        if prev is not None and prev.shape == small.shape:
            motion.append(float(np.abs(small - prev).mean()))
        prev = small
        if progress:
            progress((j + 1) / len(idxs))

    fc = int(np.median(face_counts)) if face_counts else 0
    pc = int(np.median(person_counts)) if person_counts else 0
    n = max(fc, pc)
    people = "one" if n <= 1 else ("few" if n <= 5 else "crowd")
    work = "beauty" if people == "one" else "blur"
    # 각도
    ff_ = float(np.mean(face_frac)) if face_frac else 1.0
    sf = float(np.mean(side_frac)) if side_frac else 0.0
    bk = float(np.mean(backish)) if backish else 0.0
    if ff_ < 0.5 or bk > 0.4:
        angle = "back"
    elif sf > 0.4:
        angle = "side"
    else:
        angle = "front"
    # 카메라
    mo = float(np.median(motion)) if motion else 0.0
    mo_jit = float(np.std(motion)) if len(motion) > 2 else 0.0
    if mo < 2.0:
        camera = "fixed"
    elif mo_jit > 3.5 or mo > 10:
        camera = "handheld"
    else:
        camera = "moving"
    # 장소: 신뢰도 낮음 → 약한 추측(사람 많고 복잡하면 시장, 아니면 실내/거리)
    place = None            # 기본은 '모름'(사용자 지정 권장). 약한 추측만:
    if pc >= 6:
        place = "market"
    detected = {"work": work, "place": place, "people": people, "angle": angle, "camera": camera}
    stats = {"faces": fc, "persons": pc, "motion": round(mo, 1)}
    return detected, stats


# ------------------------------------------------------------ 속성 → 보정값
STRENGTH = {"weak": 0, "mid": 1, "strong": 2}


def resolve(scene, detected):
    """scene(사용자 지정, auto 는 detected 로) → 확정 속성."""
    out = {}
    for key, _, _ in ROWS:
        v = scene.get(key, "auto")
        out[key] = detected.get(key) if v == "auto" else v
    return out


def to_params(scene, detected, strength="mid"):
    """확정 속성 → (mode, params). strength: weak|mid|strong."""
    a = resolve(scene, detected)
    work = a.get("work") or "blur"
    s = STRENGTH.get(strength, 1)
    if work == "beauty":
        p = dict(BEAUTY_DEFAULT)
        p["strength"] = [35, 55, 75][s]
        p["texture"] = [50, 35, 22][s]
        if a.get("angle") == "side":
            p["size"] = 160           # 옆얼굴은 타원을 조금 더 넉넉히
        return "beauty", p
    p = dict(BLUR_DEFAULT)
    p["strength"] = [60, 72, 85][s]
    people = a.get("people")
    if people == "crowd":
        p["sensitivity"] = 85
        p["min_size"] = 10
        p["link"] = 150
    elif people == "few":
        p["sensitivity"] = 70
        p["min_size"] = 16
    place = a.get("place")
    if place in ("market", "event"):      # 사람 밀집 → 작은 얼굴·재등장 많음
        p["min_size"] = min(p["min_size"], 10)
        p["link"] = max(p["link"], 150)
    angle = a.get("angle")
    p["body"] = angle in ("back", "side") or people == "crowd"
    cam = a.get("camera")
    p["stabilize"] = {"fixed": 20, "moving": 40, "handheld": 60}.get(cam, 50)
    if cam == "handheld":
        p["hold"] = max(p["hold"], 20)
    return "blur", p
