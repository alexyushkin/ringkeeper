"""The drawing is written by scripts/how_it_works.py: both copies stay current."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "how_it_works.py"


@pytest.fixture
def drawing() -> ModuleType:
    spec = importlib.util.spec_from_file_location("how_it_works", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_docs_and_the_app_have_the_current_drawing(drawing: ModuleType) -> None:
    css = read(drawing.CSS_FILE)
    assert read(drawing.DOCS_FILE) == drawing.docs_svg(css)
    assert read(drawing.APP_FILE) == drawing.app_module()


def test_the_drawing_uses_the_app_s_colors_in_both_themes(drawing: ModuleType) -> None:
    light, dark = drawing.palettes(read(drawing.CSS_FILE))
    for token in drawing.TOKENS:
        assert token in light
        assert token in dark
