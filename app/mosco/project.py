"""데이터: 소스(모드·뷰티/블러 값), 프로젝트 저장, 프리셋, 설정."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths

# 출력 이름에서 빼는 꼬리표(v0.7.61): 소스 이름의 "(클린)" 등. 괄호 종류·앞뒤 공백·대소문자 무시.
STRIP_TAGS = ("클린", "clean")
_TAG_RE = re.compile(r"[\s_\-]*[\(\[（]\s*(?:" + "|".join(STRIP_TAGS) + r")\s*[\)\]）]", re.IGNORECASE)


def out_stem(name: str) -> str:
    """출력 파일 이름의 몸통: 소스 이름에서 "(클린)" 같은 꼬리표를 뺌. 다 빠져 비면 원래 이름."""
    s = _TAG_RE.sub("", name).strip(" _-")
    return s or name


BEAUTY_DEFAULT = {
    "strength": 50, "wrinkle": 50, "texture": 35, "grain": 15, "skin_range": 40, "unify": 20,   # v0.7.57 피부 범위 50→40
    "size": 165, "feather": 20, "neck": 40, "stabilize": 50, "skin_ref": None, "features": True,
    "skin_extra": [],    # v0.7.63 추가 피부색(최대 2개, [cb, cr, y]) — 섀도 등 피부색이 아닌 화장을 피부로 판정
    "soft": 0, "toneup": 0,   # v0.7.64 소프트(피부만 약한 블러) · 톤업(피부 밝기) — 기본 0 = 끔
    "eye_hole": 100,     # v0.7.63 눈 제외 크기 %(0 = 눈 구멍 없음 … 100 = 기본 … 200 = 넓게). 눈썹·입은 [눈·입 제외]
}
BLUR_DEFAULT = {
    "kind": "blur", "strength": 70, "margin": 160, "feather": 10, "stabilize": 50,
    "sensitivity": 75, "min_size": 12, "hold": 15, "link": 90, "body": True,
    "profile": 50,      # v0.7.59 옆모습 확장: 옆으로 돌아간 얼굴은 타원을 뒤통수 쪽으로 넓힘(0 = 끔)
}
# 간판 블러(v0.7.8): 얼굴 블러와 같은 구성 + 최대 개수. 여유 = 글자 높이 기준 %, 최소 크기 = 글자 높이 px
SIGN_DEFAULT = {
    "kind": "blur", "strength": 70, "margin": 60, "feather": 10, "stabilize": 50,
    "sensitivity": 60, "min_size": 12, "hold": 24, "link": 90, "max_count": 20,
}
# 슬라이더 정의: 키, 이름, 최소, 최대, 표시 단위
BEAUTY_SLIDERS = [
    ("strength", "매끈 강도", 0, 100, ""), ("wrinkle", "주름 메우기", 0, 100, ""),
    ("texture", "질감 보존", 0, 100, ""), ("grain", "그레인", 0, 100, ""),
    ("skin_range", "피부 범위", 0, 100, ""), ("unify", "색 통일", 0, 100, ""),
    ("soft", "소프트", 0, 100, ""), ("toneup", "톤업", 0, 100, ""),
    ("eye_hole", "눈 제외 크기", 0, 200, "%"),
]
BEAUTY_ELLIPSE = [
    ("size", "크기", 100, 220, "%"), ("feather", "페더", 0, 30, "%"), ("neck", "목 확장", 0, 100, ""),
    ("stabilize", "떨림 보정", 0, 100, ""),
]
BLUR_SLIDERS = [("strength", "강도", 0, 100, "")]
BLUR_ELLIPSE = [("margin", "여유", 100, 250, "%"), ("profile", "옆모습 확장", 0, 100, ""), ("feather", "페더", 0, 30, "%"),
                ("stabilize", "떨림 보정", 0, 100, "")]
BLUR_DETECT = [("sensitivity", "민감도", 0, 100, ""), ("min_size", "최소 크기", 8, 100, "px"), ("hold", "놓침 유지", 0, 60, "f"),
               ("link", "다시 잇기", 0, 300, "f")]
SIGN_SLIDERS = [("strength", "강도", 0, 100, "")]
SIGN_ELLIPSE = [("margin", "여유", 0, 200, "%"), ("feather", "페더", 0, 30, "%"), ("stabilize", "떨림 보정", 0, 100, "")]
SIGN_DETECT = [("sensitivity", "민감도", 0, 100, ""), ("min_size", "최소 크기", 6, 100, "px"), ("hold", "놓침 유지", 0, 60, "f"),
               ("link", "다시 잇기", 0, 300, "f"), ("max_count", "최대 개수", 1, 50, "")]

RENDER_DEFAULT = {
    "out_dir": "", "name": "", "main": True, "prores": "422 HQ", "overlay": True, "review": False,
    "audio": True, "hw": False, "two_pass": True, "gpu": False,
    "workers": 0,      # 렌더 작업자(스레드) 수. 0 = 자동(코어-2, 최대 12)
}


@dataclass
class Source:
    path: str
    mode: str = "beauty"                     # beauty | blur
    beauty: dict = field(default_factory=lambda: dict(BEAUTY_DEFAULT))
    blur: dict = field(default_factory=lambda: dict(BLUR_DEFAULT))
    sign: dict = field(default_factory=lambda: dict(SIGN_DEFAULT))
    custom: bool = False                     # 기본값에서 바뀜(C)
    edits: dict = field(default_factory=dict)   # 모드별 타원 수정(edits.py)
    both: bool = False                       # 블러: 얼굴·간판 둘 다(활성 편집 = mode, 나머지 한 종류도 처리)

    @property
    def name(self) -> str:
        return Path(self.path).stem

    def params(self) -> dict:
        return self.params_for(self.mode)

    def params_for(self, mode) -> dict:
        return {"beauty": self.beauty, "blur": self.blur, "sign": self.sign}.get(mode, self.blur)

    def other_mode(self) -> str:
        return "sign" if self.mode == "blur" else "blur"

    def blur_kinds(self) -> list:
        """처리할 블러 종류(활성 mode 먼저, both 면 나머지도). 뷰티면 빈 목록."""
        if self.mode not in ("blur", "sign"):
            return []
        return [self.mode, self.other_mode()] if self.both else [self.mode]

    @staticmethod
    def from_dict(d) -> "Source":
        s = Source(d["path"], d.get("mode", "beauty"))
        s.beauty.update(d.get("beauty", {}))
        s.blur.update(d.get("blur", {}))
        s.sign.update(d.get("sign", {}))
        s.custom = d.get("custom", False)
        s.edits = d.get("edits", {}) or {}
        s.both = bool(d.get("both", False))
        return s


@dataclass
class Project:
    sources: list = field(default_factory=list)
    file: str = ""

    def to_dict(self):
        return {"app": "MOSCO", "v": 1, "sources": [asdict(s) for s in self.sources]}

    def save(self, f):
        Path(f).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        self.file = str(f)

    @staticmethod
    def load(f) -> "Project":
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        p = Project([Source.from_dict(x) for x in d.get("sources", [])], str(f))
        return p

    def snapshot(self):
        return copy.deepcopy(self.to_dict())

    def restore(self, snap):
        self.sources = [Source.from_dict(x) for x in snap["sources"]]


# ------------------------------------------------------------ 설정(settings.json)
class Settings:
    def __init__(self):
        self.d = {"render": dict(RENDER_DEFAULT), "beauty": dict(BEAUTY_DEFAULT), "blur": dict(BLUR_DEFAULT),
                  "sign": dict(SIGN_DEFAULT),
                  "last_folder": "", "default_mode": "beauty", "view_scale": 0.5}
        try:
            j = json.loads(paths.settings_path().read_text(encoding="utf-8"))
            for k in ("render", "beauty", "blur", "sign"):
                self.d[k].update(j.get(k, {}))
            for k in ("last_folder", "default_mode", "view_scale"):
                self.d[k] = j.get(k, self.d[k])
            if not j.get("skin_range40"):
                # v0.7.57 한 번만: 예전 기본값(50)으로 저장된 사용자 기본값·기본 프리셋 → 40. 직접 바꾼 값(50 아님)은 그대로
                if self.d["beauty"].get("skin_range") == 50:
                    self.d["beauty"]["skin_range"] = 40
                _migrate_presets_skin_range()
                self.d["skin_range40"] = True
                self.save()
        except Exception:  # noqa: BLE001
            pass

    def save(self):
        try:
            paths.settings_path().write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    @property
    def render(self) -> dict:
        return self.d["render"]

    def defaults(self, mode) -> dict:
        return dict(self.d[mode])


# ------------------------------------------------------------ 프리셋(presets/*.json)
def list_presets() -> list[str]:
    return sorted(p.stem for p in paths.preset_dir().glob("*.json"))


def load_preset(name) -> dict:
    """프리셋 읽기. 깨진 파일(비었거나 다른 인코딩)이면: 기본 프리셋은 새로 만들어 돌려주고, 그 밖은 이유를 알림(v0.7.61)."""
    f = paths.preset_dir() / f"{name}.json"
    try:
        raw = f.read_bytes()
        try:
            txt = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            txt = raw.decode("cp949")
        d = json.loads(txt)
        if not isinstance(d, dict):
            raise ValueError("형식이 다릅니다")
        return d
    except Exception as e:  # noqa: BLE001
        made = _builtin_preset(name)
        if made is not None:
            save_preset(name, made["mode"], made["params"])
            return made
        raise ValueError(f"프리셋 파일을 읽을 수 없습니다: {f}\n({e})\n프리셋을 우클릭해 삭제한 뒤 다시 저장하세요.") from e


def _builtin_preset(name):
    b = dict(BEAUTY_DEFAULT)
    table = {"인터뷰 기본": ("beauty", b), "인터뷰 약하게": ("beauty", {**b, "strength": 30, "texture": 50}),
             "인터뷰 강하게": ("beauty", {**b, "strength": 75, "texture": 25}),
             "시장 블러": ("blur", dict(BLUR_DEFAULT)), "시장 모자이크": ("blur", {**BLUR_DEFAULT, "kind": "mosaic"})}
    if name not in table:
        return None
    mode, params = table[name]
    return {"mode": mode, "params": params}


def save_preset(name, mode, params):
    (paths.preset_dir() / f"{name}.json").write_text(
        json.dumps({"mode": mode, "params": params}, ensure_ascii=False, indent=1), encoding="utf-8")


def delete_preset(name):
    (paths.preset_dir() / f"{name}.json").unlink(missing_ok=True)


def rename_preset(old, new):
    (paths.preset_dir() / f"{old}.json").rename(paths.preset_dir() / f"{new}.json")


BUILTIN_BEAUTY = ("인터뷰 기본", "인터뷰 약하게", "인터뷰 강하게")


def _migrate_presets_skin_range():
    """기본 뷰티 프리셋 3개의 피부 범위가 예전 기본값(50)이면 40으로(v0.7.57). 사용자가 만든 프리셋은 손대지 않음."""
    for name in BUILTIN_BEAUTY:
        f = paths.preset_dir() / f"{name}.json"
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            prm = d.get("params", d)
            if prm.get("skin_range") == 50:
                prm["skin_range"] = 40
                f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def ensure_builtin_presets():
    """처음 실행 시 기본 프리셋 몇 개."""
    if list_presets():
        return
    b = dict(BEAUTY_DEFAULT)
    save_preset("인터뷰 기본", "beauty", b)
    save_preset("인터뷰 약하게", "beauty", {**b, "strength": 30, "texture": 50})
    save_preset("인터뷰 강하게", "beauty", {**b, "strength": 75, "texture": 25})
    save_preset("시장 블러", "blur", dict(BLUR_DEFAULT))
    save_preset("시장 모자이크", "blur", {**BLUR_DEFAULT, "kind": "mosaic"})
