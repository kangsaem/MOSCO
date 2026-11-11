"""AUTO 보조: 작업(뷰티/블러)은 사용자가 고르고, 강도(약/중/강)로 값을 만든다.

장면 '분류'(시장/정면/고정 …)는 부정확해서 뺐다. 대신 블러에 실제로 쓸모 있는 값만
'직접 측정'한다: 그 클립에서 실제로 잡힌 가장 작은 얼굴 → 최소 크기, 실측 카메라 움직임 → 떨림 보정.
결과는 '출발점'일 뿐 — 사용자가 고치거나 렌더에서 다듬는다.
"""
from __future__ import annotations

import numpy as np

from . import decoder, ff, models
from .project import BEAUTY_DEFAULT, BLUR_DEFAULT

LABEL = {"beauty": "뷰티", "blur": "블러", "sign": "간판", "weak": "약", "mid": "중", "strong": "강"}
STRENGTH = {"weak": 0, "mid": 1, "strong": 2}

DET_W, DET_H = 960, 540      # 측정용 다운스케일


# ------------------------------------------------------------ 측정(분류 아님)
def _sample_indices(n, k=8):
    if n <= k:
        return list(range(n))
    return [int(round(i * (n - 1) / (k - 1))) for i in range(k)]


def measure(info: ff.Info, cancel=None, progress=None):
    """고르게 뽑은 8장으로 '측정'. 반환: {faces, persons, min_face(원본 px), motion}.

    분류(장소·각도 등)는 하지 않는다. 블러 파라미터 자동값과 화면 표시에만 쓴다.
    """
    idxs = _sample_indices(info.frames, 8)
    face_counts, person_counts = [], []
    min_face = None
    prev, motion = None, []
    scale = info.w / DET_W      # 측정 해상도 → 원본 px 환산
    for j, i in enumerate(idxs):
        if cancel is not None and cancel.is_set():
            from .analyze import Cancelled
            raise Cancelled()
        fr = decoder.grab(info, i, 8, (DET_W, DET_H))
        faces = models.detector().detect(fr, 0.5, tiles=True, min_size=10)
        persons = models.person_detector().detect(fr, 0.4)
        face_counts.append(len(faces))
        person_counts.append(max(len(faces), len(persons)))
        for d in faces:
            x0, y0, x1, y1 = d.box[:4]
            short = min(abs(x1 - x0), abs(y1 - y0))
            if short > 0:
                min_face = short if min_face is None else min(min_face, short)
        small = fr[::8, ::8].astype(np.float32).mean(-1)
        if prev is not None and prev.shape == small.shape:
            motion.append(float(np.abs(small - prev).mean()))
        prev = small
        if progress:
            progress((j + 1) / len(idxs))

    fc = int(np.median(face_counts)) if face_counts else 0
    pc = int(np.median(person_counts)) if person_counts else 0
    mf = int(round(min_face * scale)) if min_face else 0
    mo = round(float(np.median(motion)), 1) if motion else 0.0
    return {"faces": fc, "persons": pc, "min_face": mf, "motion": mo}


# ------------------------------------------------------------ 작업+강도(+측정) → 보정값
def level_of(mode, params):
    """현재 설정이 약·중·강 중 어느 것인지(v0.7.60). 강도를 가르는 값(뷰티: 매끈·주름·질감 / 블러·간판: 강도)이
    정확히 같을 때만 "weak"/"mid"/"strong", 직접 조정했으면 None."""
    keys = ("strength", "wrinkle", "texture") if mode == "beauty" else ("strength",)
    for lv in ("weak", "mid", "strong"):
        _, p = to_params(mode, lv)
        if all(params.get(k) == p.get(k) for k in keys):
            return lv
    return None


def to_params(mode, strength="mid", measured=None):
    """사용자가 고른 작업(beauty/blur)과 강도 → (mode, params). 블러는 측정값 일부 자동 반영."""
    s = STRENGTH.get(strength, 1)
    if mode == "beauty":
        p = dict(BEAUTY_DEFAULT)
        p["strength"] = [35, 55, 75][s]       # 매끈
        p["wrinkle"] = [30, 50, 55][s]        # 주름 메우기(v0.7.61 강 70→55: 70 에서는 주름 골이 밝게 뒤집혀 보이는 경우가 있음)
        p["texture"] = [55, 40, 28][s]        # 질감 보존
        return "beauty", p
    if mode == "sign":                        # 간판: 강도만(약·중·강), 나머지는 기본값
        from .project import SIGN_DEFAULT
        p = dict(SIGN_DEFAULT)
        p["strength"] = [60, 72, 85][s]
        return "sign", p
    p = dict(BLUR_DEFAULT)
    p["strength"] = [60, 72, 85][s]
    p["body"] = True
    if measured:
        mf = measured.get("min_face") or 0
        if mf:
            p["min_size"] = max(8, min(40, int(mf * 0.8)))   # 실제 최소 얼굴보다 약간 작게 잡아 놓침 방지
        mo = measured.get("motion", 0.0)
        p["stabilize"] = 20 if mo < 2.0 else (60 if mo > 10 else 40)
    return "blur", p
