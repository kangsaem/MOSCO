"""데이터: 소스(모드·뷰티/블러 값), 프로젝트 저장, 프리셋, 설정."""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths

BEAUTY_DEFAULT = {
    "strength": 50, "texture": 35, "grain": 15, "skin_range": 50, "unify": 20,
    "size": 145, "feather": 8, "neck": 0, "stabilize": 50, "skin_ref": None,
}
BLUR_DEFAULT = {
    "kind": "blur", "strength": 70, "margin": 160, "feather": 10, "stabilize": 50,
    "sensitivity": 75, "min_size": 16, "hold": 15,
}
# 슬라이더 정의: 키, 이름, 최소, 최대, 표시 단위
BEAUTY_SLIDERS = [
    ("strength", "강도", 0, 100, ""), ("texture", "질감 보존", 0, 100, ""), ("grain", "그레인", 0, 100, ""),
    ("skin_range", "피부 범위", 0, 100, ""), ("unify", "색 통일", 0, 100, ""),
]
BEAUTY_ELLIPSE = [
    ("size", "크기", 100, 220, "%"), ("feather", "페더", 0, 30, "%"), ("neck", "목 확장", 0, 100, ""),
    ("stabilize", "떨림 보정", 0, 100, ""),
]
BLUR_SLIDERS = [("strength", "강도", 0, 100, "")]
BLUR_ELLIPSE = [("margin", "여유", 100, 250, "%"), ("feather", "페더", 0, 30, "%"), ("stabilize", "떨림 보정", 0, 100, "")]
BLUR_DETECT = [("sensitivity", "민감도", 0, 100, ""), ("min_size", "최소 크기", 8, 100, "px"), ("hold", "놓침 유지", 0, 60, "f")]

RENDER_DEFAULT = {
    "out_dir": "", "name": "", "main": True, "prores": "422 HQ", "overlay": True, "review": False,
    "audio": True, "hw": False, "two_pass": True,
}


@dataclass
class Source:
    path: str
    mode: str = "beauty"                     # beauty | blur
    beauty: dict = field(default_factory=lambda: dict(BEAUTY_DEFAULT))
    blur: dict = field(default_factory=lambda: dict(BLUR_DEFAULT))
    custom: bool = False                     # 기본값에서 바뀜(C)

    @property
    def name(self) -> str:
        return Path(self.path).stem

    def params(self) -> dict:
        return self.beauty if self.mode == "beauty" else self.blur

    @staticmethod
    def from_dict(d) -> "Source":
        s = Source(d["path"], d.get("mode", "beauty"))
        s.beauty.update(d.get("beauty", {}))
        s.blur.update(d.get("blur", {}))
        s.custom = d.get("custom", False)
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
                  "last_folder": "", "default_mode": "beauty"}
        try:
            j = json.loads(paths.settings_path().read_text(encoding="utf-8"))
            for k in ("render", "beauty", "blur"):
                self.d[k].update(j.get(k, {}))
            for k in ("last_folder", "default_mode"):
                self.d[k] = j.get(k, self.d[k])
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
    return json.loads((paths.preset_dir() / f"{name}.json").read_text(encoding="utf-8"))


def save_preset(name, mode, params):
    (paths.preset_dir() / f"{name}.json").write_text(
        json.dumps({"mode": mode, "params": params}, ensure_ascii=False, indent=1), encoding="utf-8")


def delete_preset(name):
    (paths.preset_dir() / f"{name}.json").unlink(missing_ok=True)


def rename_preset(old, new):
    (paths.preset_dir() / f"{old}.json").rename(paths.preset_dir() / f"{new}.json")


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
