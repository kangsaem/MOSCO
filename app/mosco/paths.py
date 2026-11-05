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


def _mk(name) -> Path:
    d = app_dir() / name
    d.mkdir(exist_ok=True)
    return d


def cache_dir() -> Path:
    return _mk("cache")


def preset_dir() -> Path:
    return _mk("presets")


def settings_path() -> Path:
    return app_dir() / "settings.json"


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
