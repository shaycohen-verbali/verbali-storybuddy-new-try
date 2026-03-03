from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from .models import StoryPackage


BASE_DIR = Path(__file__).resolve().parent.parent
if os.getenv("STORYBUDDY_DATA_DIR"):
    DATA_DIR = Path(os.getenv("STORYBUDDY_DATA_DIR", "")).expanduser()
elif os.getenv("VERCEL"):
    DATA_DIR = Path("/tmp/storybuddy/packages")
else:
    DATA_DIR = BASE_DIR / "backend_data" / "packages"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _package_path(package_id: str) -> Path:
    return DATA_DIR / f"{package_id}.json"


def save_package(package: StoryPackage) -> None:
    path = _package_path(package.id)
    path.write_text(package.model_dump_json(by_alias=True, indent=2), encoding="utf-8")


def load_package(package_id: str) -> Optional[StoryPackage]:
    path = _package_path(package_id)
    if not path.exists():
        return None
    return StoryPackage.model_validate_json(path.read_text(encoding="utf-8"))


def list_packages() -> List[StoryPackage]:
    packages: List[StoryPackage] = []
    for path in sorted(DATA_DIR.glob("*.json")):
        try:
            packages.append(StoryPackage.model_validate_json(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    packages.sort(key=lambda pkg: pkg.updated_at, reverse=True)
    return packages


def delete_package(package_id: str) -> bool:
    path = _package_path(package_id)
    if not path.exists():
        return False
    path.unlink()
    return True
