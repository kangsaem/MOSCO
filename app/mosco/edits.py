"""타원 수정: 키프레임(보정량 방식) · 타원 추가 · 끄기 · 합치기 · 타원별 값.

데이터(Source.edits[mode]):
  {"groups": [Group...], "merges": [[앵커A, 앵커B], ...], "next": 다음 gid}
  Group = {"gid", "manual": bool, "anchor": [f, cx, cy], "keys": {"f": [cx, cy, w, h]},
           "off": [[a, b], ...], "ov": {"size": %, "feather": %, "strength": 0~100}}

- 자동 타원의 키 = 그 프레임에서 고친 결과(절대값). 적용할 때 자동값과의 차이(보정량)를 키 사이에 보간하고,
  첫/마지막 키 밖으로는 FADE 프레임 동안 0 으로 줄어듦 → 틀린 몇 프레임만 고치면 나머지는 계속 따라감
- 자동 타원이 없는 프레임(놓침)은 키 사이에서 키 값 그대로 보간 → 놓친 구간도 키로 메울 수 있음
- 추가 타원(manual)은 첫 키부터 나타나고 마지막 키 뒤로는 마지막 값 유지(끄기로 끝냄)
- 분석 값을 바꿔 ID 가 다시 매겨져도: 그룹의 앵커(프레임·위치)에 가장 가까운 트랙에 다시 붙음
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

import numpy as np

from . import analyze

FADE = 12


@dataclass
class Item:
    key: tuple                 # ("g", gid) 또는 ("t", tid) — 선택 식별
    tid: int | None            # 표시 ID(자동 트랙) — 추가 타원은 None
    ell: tuple                 # cx, cy, w, h, angle
    pts: object = None         # 뷰티 랜드마크(있으면 눈·입 제외)
    presence: float = 1.0
    ov: dict = field(default_factory=dict)
    manual: bool = False
    gid: int | None = None
    keyed: bool = False        # 이 프레임에 키 있음


def empty():
    return {"groups": [], "merges": [], "next": 1}


def get(src, mode=None) -> dict:
    mode = mode or src.mode
    e = src.edits.setdefault(mode, empty())
    e.setdefault("groups", []); e.setdefault("merges", []); e.setdefault("next", 1)
    return e


def _off(g, i):
    for a, b in g.get("off", []):
        if a <= i <= b:
            return True
    return False


def _interp_keys(ks, vals, i):
    """ks 정렬된 프레임, vals 같은 길이(np 배열). 구간 안 선형 보간, 밖 None."""
    if not ks:
        return None
    if i < ks[0] or i > ks[-1]:
        return None
    j = bisect.bisect_left(ks, i)
    if ks[j] == i:
        return vals[j]
    a, b = ks[j - 1], ks[j]
    u = (i - a) / (b - a)
    return vals[j - 1] * (1 - u) + vals[j] * u


class Resolver:
    """한 소스·한 트랙에 대한 최종 타원 목록(프레임별)."""

    def __init__(self, src, track):
        self.src = src
        self.mode = src.mode
        self.p = src.params()
        self.track = track
        self.e = get(src)
        self.n = track.n if track is not None else 0
        self.g_tid = {}            # gid -> tid
        self.merge = {}            # tid_b -> tid_a
        self.bridge = {}           # tid_a -> {frame: box}  (합친 두 트랙 사이 빈 구간)
        if track is not None:
            for g in self.e["groups"]:
                if not g.get("manual"):
                    t = self.find_tid(*g["anchor"])
                    if t is not None:
                        self.g_tid[g["gid"]] = t
            if self.mode == "blur":
                self._merges()
        self.tid_g = {t: g for g, t in self.g_tid.items()}
        self.groups = {g["gid"]: g for g in self.e["groups"]}
        self._prep = {}

    # ------------------------------------------------------------ 자동 타원
    def auto_at(self, i):
        """[(tid, ell, pts, presence)] 자동 결과(합치기 반영)."""
        tr = self.track
        if tr is None:
            return []
        if self.mode == "beauty":
            return [(slot + 1, analyze.head_ellipse(pts, self.p["size"], self.p["neck"]), pts, pres)
                    for slot, pts, pres in tr.at(i)]
        out = {}
        for f in tr.at(i):
            tid = self.merge.get(f[0], f[0])
            if tid in out:
                continue
            out[tid] = (tid, analyze.face_to_head(*f[1:5], self.p["margin"]), None, 1.0)
        for tid, fr in self.bridge.items():
            if i in fr and tid not in out:
                out[tid] = (tid, analyze.face_to_head(*fr[i], self.p["margin"]), None, 1.0)
        return list(out.values())

    def find_tid(self, f, cx, cy):
        best, bd = None, 1e9
        for tid, ell, _, _ in self._raw_auto(int(f)):
            d = np.hypot(ell[0] - cx, ell[1] - cy) / max(ell[2], ell[3], 1)
            if d < 0.8 and d < bd:
                best, bd = tid, d
        return best

    def _raw_auto(self, i):
        tr = self.track
        if tr is None:
            return []
        if self.mode == "beauty":
            return [(slot + 1, analyze.head_ellipse(pts, self.p["size"], self.p["neck"]), pts, pres)
                    for slot, pts, pres in tr.at(i)]
        return [(f[0], analyze.face_to_head(*f[1:5], self.p["margin"]), None, 1.0) for f in tr.at(i)]

    def _merges(self):
        tracks = self.track.tracks
        for A, B in self.e["merges"]:
            ta, tb = self.find_tid(*A), self.find_tid(*B)
            ta, tb = self.merge.get(ta, ta), self.merge.get(tb, tb)
            if ta is None or tb is None or ta == tb:
                continue
            ra, rb = tracks[ta], tracks[tb]
            if rb["f0"] < ra["f0"]:
                ta, tb, ra, rb = tb, ta, rb, ra
            self.merge[tb] = ta
            for k, v in list(self.merge.items()):
                if v == tb:
                    self.merge[k] = ta
            # 빈 구간 보간
            ea = ra["f0"] + len(ra["box"]) - 1
            sb = rb["f0"]
            if sb > ea + 1:
                a, b = ra["box"][-1], rb["box"][0]
                fr = self.bridge.setdefault(ta, {})
                for f in range(ea + 1, sb):
                    u = (f - ea) / (sb - ea)
                    fr[f] = a * (1 - u) + b * u

    # ------------------------------------------------------------ 그룹 준비(키 정렬, 보정량)
    def _g(self, g):
        gid = g["gid"]
        if gid in self._prep:
            return self._prep[gid]
        ks = sorted(int(k) for k in g.get("keys", {}))
        vals = [np.array(g["keys"][str(k)], np.float32) for k in ks]
        offs = []
        tid = self.g_tid.get(gid)
        for k, v in zip(ks, vals):
            au = self._auto_tid(tid, k) if tid is not None else None
            offs.append(None if au is None else v - np.array(au[:4], np.float32))
        r = (ks, vals, offs, tid)
        self._prep[gid] = r
        return r

    def _auto_tid(self, tid, i):
        for t, ell, _, _ in self.auto_at(i):
            if t == tid:
                return ell
        return None

    def _offset(self, ks, offs, i):
        good = [(k, o) for k, o in zip(ks, offs) if o is not None]
        if not good:
            return None
        gk = [k for k, _ in good]
        gv = [o for _, o in good]
        if i < gk[0]:
            w = 1 - (gk[0] - i) / FADE
            return gv[0] * w if w > 0 else None
        if i > gk[-1]:
            w = 1 - (i - gk[-1]) / FADE
            return gv[-1] * w if w > 0 else None
        return _interp_keys(gk, gv, i)

    # ------------------------------------------------------------ 최종
    def items(self, i) -> list[Item]:
        out = []
        seen = set()
        for tid, ell, pts, pres in self.auto_at(i):
            gid = self.tid_g.get(tid)
            g = self.groups.get(gid) if gid is not None else None
            if g is not None:
                if g.get("off_all") or _off(g, i):
                    seen.add(gid)
                    continue
                ks, vals, offs, _ = self._g(g)
                o = self._offset(ks, offs, i)
                if o is not None:
                    ell = (*(np.array(ell[:4]) + o), ell[4])
                seen.add(gid)
            out.append(Item(("g", gid) if gid is not None else ("t", tid), tid, ell, pts, pres,
                            dict(g.get("ov", {})) if g else {}, False, gid,
                            bool(g and str(i) in g.get("keys", {}))))
        for g in self.e["groups"]:
            gid = g["gid"]
            if gid in seen or g.get("off_all") or _off(g, i):
                continue
            ks, vals, offs, tid = self._g(g)
            if not ks:
                continue
            if g.get("manual"):
                # 수동(그린) 타원은 클립 처음~끝 존재: 첫 키 이전엔 첫 값, 마지막 키 이후엔 마지막 값
                if i <= ks[0]:
                    v = vals[0]
                elif i >= ks[-1]:
                    v = vals[-1]
                else:
                    v = _interp_keys(ks, vals, i)
            else:       # 자동 그룹인데 이 프레임에 자동 타원이 없음 → 키 구간 안에서만
                v = _interp_keys(ks, vals, i)
                if v is None:
                    continue
            out.append(Item(("g", gid), tid, (*v, 0.0), None, 1.0, dict(g.get("ov", {})), bool(g.get("manual")),
                            gid, str(i) in g.get("keys", {})))
        return out

    def keys_of(self, key):
        """선택한 타원의 키 프레임 목록."""
        if key and key[0] == "g" and key[1] in self.groups:
            return sorted(int(k) for k in self.groups[key[1]].get("keys", {}))
        return []


# ------------------------------------------------------------ 편집 동작(상태 변경은 state 에서 실행취소와 함께)
def ensure_group(src, item: Item, frame) -> dict:
    """선택한 타원의 그룹(없으면 만듦)."""
    e = get(src)
    if item.gid is not None:
        for g in e["groups"]:
            if g["gid"] == item.gid:
                return g
    g = {"gid": e["next"], "manual": False, "anchor": [int(frame), float(item.ell[0]), float(item.ell[1])],
         "keys": {}, "off": [], "ov": {}}
    e["next"] += 1
    e["groups"].append(g)
    return g


def add_manual(src, frame, ell) -> dict:
    e = get(src)
    g = {"gid": e["next"], "manual": True, "anchor": [int(frame), float(ell[0]), float(ell[1])],
         "keys": {str(int(frame)): [float(v) for v in ell[:4]]}, "off": [], "ov": {}}
    e["next"] += 1
    e["groups"].append(g)
    return g


def set_key(g, frame, ell):
    g.setdefault("keys", {})[str(int(frame))] = [float(v) for v in ell[:4]]


def del_key(g, frame):
    g.get("keys", {}).pop(str(int(frame)), None)


def off_range(g, a, b):
    g.setdefault("off", []).append([int(a), int(b)])


def on_all(g):
    g["off"] = []
    g["off_all"] = False


def remove_group(src, gid):
    e = get(src)
    e["groups"] = [g for g in e["groups"] if g["gid"] != gid]


def add_merge(src, a_anchor, b_anchor):
    get(src)["merges"].append([list(a_anchor), list(b_anchor)])
