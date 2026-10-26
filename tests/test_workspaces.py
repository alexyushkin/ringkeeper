"""Which folder a desktop project gets (services.workspaces.choose)."""

from pathlib import Path

import pytest

from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.services.workspaces import choose


def settings(data: Path, workspaces: Path) -> Settings:
    return Settings(mode="desktop", data_dir=data, workspaces_root=str(workspaces))


def test_an_empty_folder_gets_one_named_after_the_project(tmp_path: Path) -> None:
    chosen = choose(settings(tmp_path / "data", tmp_path / "work"), "", "acme-web")
    assert chosen == tmp_path / "work" / "acme-web"


def test_the_workspaces_folder_may_be_inside_the_data_folder(tmp_path: Path) -> None:
    """As DESKTOP_REAL_TASK_CHECK.md sets it up: a project folder there is fine."""
    data = tmp_path / "data"
    configured = settings(data, data / "workspaces")
    assert choose(configured, "", "acme-web") == data / "workspaces" / "acme-web"
    assert choose(configured, "globex", "x") == data / "workspaces" / "globex"


@pytest.mark.parametrize("inside", ["", "vault", "workers/ringkeeper-state-1"])
def test_ringkeeper_s_own_data_stays_off_limits(tmp_path: Path, inside: str) -> None:
    data = tmp_path / "data"
    with pytest.raises(RuleViolationError, match="own data folder"):
        choose(settings(data, data / "workspaces"), str(data / inside), "acme-web")


def test_the_workspaces_folder_itself_is_not_a_project_folder(tmp_path: Path) -> None:
    data = tmp_path / "data"
    with pytest.raises(RuleViolationError, match="own data folder"):
        choose(settings(data, data / "workspaces"), str(data / "workspaces"), "acme-web")
