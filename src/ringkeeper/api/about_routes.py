"""Settings > About: the version, and the third-party notices the desktop app ships."""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from ringkeeper import __version__
from ringkeeper.api.auth import AdminDep
from ringkeeper.config import get_settings

NOTICES_FILE = "THIRD-PARTY-NOTICES.txt"
COMPONENTS_FILE = "components.json"

router = APIRouter(prefix="/about", dependencies=[AdminDep])


def _notices() -> Path | None:
    folder = get_settings().notices_dir
    return folder if folder is not None and (folder / NOTICES_FILE).is_file() else None


@router.get("")
def about() -> dict[str, Any]:
    settings = get_settings()
    folder = _notices()
    components: list[dict[str, str]] = []
    if folder is not None and (folder / COMPONENTS_FILE).is_file():
        components = json.loads((folder / COMPONENTS_FILE).read_text(encoding="utf-8"))
    return {
        "version": settings.app_version or __version__,
        "mode": settings.mode,
        "components": components,
        "notices": folder is not None,
    }


@router.get("/notices")
def notices() -> dict[str, str]:
    folder = _notices()
    if folder is None:
        return {"text": "The third-party notices ship with the desktop app."}
    return {"text": (folder / NOTICES_FILE).read_text(encoding="utf-8")}
