"""Browser (Playwright) as one click on your own tools (services.browser, ringkeeper.browser):
what it installs and where its profile is, what runs and what waits, and "Add a browser" on a
task that needed one."""

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from ringkeeper import browser
from ringkeeper.adapters import mcp
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import InstanceType, TaskStatus
from ringkeeper.services import browser as browser_service
from ringkeeper.services import calls, clients, instances, link_offer, local_servers, tasks
from tests import test_web_api
from tests.helpers import make_instance, make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

CONFIG = {"source": browser.SOURCE}


def level(tool: str, arguments: dict[str, Any] | None = None) -> str:
    return mcp.call_rule(CONFIG, tool, arguments).level


def test_going_and_reading_run() -> None:
    for tool in ("browser_navigate", "browser_snapshot", "browser_take_screenshot",
                 "browser_tabs", "browser_wait_for", "browser_navigate_back"):  # fmt: skip
        assert level(tool) == mcp.READ, tool
    assert level("browser_click", {"element": "Pricing link", "ref": "e12"}) == mcp.READ
    assert level("browser_press_key", {"key": "ArrowDown"}) == mcp.READ


def test_forms_sends_and_downloads_wait() -> None:
    for tool in ("browser_type", "browser_fill_form", "browser_select_option",
                 "browser_file_upload", "browser_handle_dialog", "browser_install"):  # fmt: skip
        assert level(tool) == mcp.APPROVE, tool
    for element in ("Submit button", "Sign in", "Place order", "Download invoice (PDF)",
                    "Log-in", "Pay now", "Export CSV"):  # fmt: skip
        assert level("browser_click", {"element": element, "ref": "e3"}) == mcp.APPROVE, element
    assert level("browser_press_key", {"key": "Enter"}) == mcp.APPROVE
    assert level("browser_some_new_tool") == mcp.APPROVE  # one it doesn't know waits


def test_code_and_saved_logins_wait_in_your_own_projects_too() -> None:
    assert browser.asks_everywhere("browser_evaluate")
    assert browser.asks_everywhere("browser_run_code_unsafe")
    assert browser.asks_everywhere("browser_cookie_list")
    assert not browser.asks_everywhere("browser_type")
    assert level("browser_cookie_list") == mcp.APPROVE


def test_your_own_choice_still_counts() -> None:
    chosen = {**CONFIG, "tool_rules": {"browser_type": mcp.READ}}
    assert mcp.call_rule(chosen, "browser_type", {"text": "x"}).level == mcp.READ


def _caller(project: str) -> Caller:
    return Caller(project=project, session_id="s1", token_id="k1")


def test_a_form_waits_in_a_client_s_project_and_runs_in_your_own(database_url: str) -> None:
    del database_url
    with session_scope() as session:
        clients.ensure(session, "Harbor Studio")
        clients.set_own(session, "harbor-studio", True)
        make_project(session, "studio-site", owner="harbor-studio")
        make_project(session, "acme-web", owner="acme")
    typing = {"tool": "browser_type", "arguments": {"text": "hello"}}
    code = {"tool": "browser_evaluate", "arguments": {"function": "() => 1"}}

    def runs(project: str, args: dict[str, Any]) -> bool:
        call = ToolCall(caller=_caller(project), service=InstanceType.MCP, instance="browser",
                        tool="call_tool", args=args)  # fmt: skip
        return anyio.run(calls._browser_runs_here, call, CONFIG)  # pyright: ignore[reportPrivateUsage]

    assert runs("studio-site", typing)  # your own project: it runs
    assert not runs("acme-web", typing)  # a client's: it waits
    assert not runs("studio-site", code)  # code waits everywhere
    with session_scope() as session:
        assert clients.owns_project(session, "studio-site")
        assert not clients.owns_project(session, "acme-web")
        assert not clients.owns_project(session, None)


def test_its_profile_is_its_own_and_its_version_is_pinned(tmp_path: Path) -> None:
    settings = get_settings().model_copy(update={"data_dir": tmp_path})
    args = browser_service.arguments(settings)
    profile = Path(args[args.index("--user-data-dir") + 1])
    assert profile == tmp_path / "browser" / "profile"  # in Ringkeeper's data, not yours
    assert args[args.index("--browser") + 1] in ("chrome", "msedge")
    found = browser_service.plan()
    assert (found.identifier, found.version) == ("@playwright/mcp", browser_service.VERSION)
    assert found.version != "latest"


def test_one_click_installs_it_linked_to_no_project(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def installed(
        _settings: Any, plan: local_servers.Plan, _values: dict[str, str]
    ) -> dict[str, Any]:
        return {"command": "node", "args": ["cli.js"], "env": {},
                "pinned": f"{plan.identifier}@{plan.version}"}  # fmt: skip

    monkeypatch.setattr(local_servers, "install", installed)
    monkeypatch.setattr(local_servers, "finder", lambda _name: "node")
    info = owner.get("/connections/browser").json()
    assert (info["added"], info["needs"]) == (False, None)
    started = owner.post("/connections/browser", json={})
    assert started.status_code == 202, started.text
    for _ in range(100):
        state = owner.get("/connections/local/browser").json()["state"]
        if state != "installing":
            break
        time.sleep(0.05)
    assert state == "done"
    with session_scope() as session:
        added = instances.get_instance(session, "browser")
        assert added.shared
        assert not added.all_projects
        assert instances.bound_project_names(added) == []  # you pick
        assert added.config["source"] == browser.SOURCE
    assert owner.get("/connections/browser").json()["added"] is True
    assert owner.post("/connections/browser", json={}).status_code == 409  # one is enough


def _not_done(project: str) -> Any:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project=project, prompt="log in and look")
        task.status, task.finished_at = TaskStatus.NOT_DONE, datetime.now(UTC)
        return link_offer.find(session, task, "browser")


@pytest.mark.usefixtures("task_registry")
def test_a_task_that_needed_a_browser_offers_one(database_url: str) -> None:
    del database_url
    with session_scope() as session:
        make_project(session, "harbor-site", owner="acme", title="Harbor site")
    offer = _not_done("harbor-site")
    assert offer is not None
    assert offer["label"] == "Add a browser to Harbor site"
    assert offer["href"].startswith("/connections/new?kind=shared&service=browser&link=harbor-site")
    with session_scope() as session:  # with one already: link it and retry
        make_instance(session, "browser", type=InstanceType.MCP, owner=instances.SHARED_OWNER,
                      shared=True, title="Browser (Playwright)",
                      config={"source": "browser", "registry": "@playwright/mcp"})  # fmt: skip
        make_instance(session, "acme-files", type=InstanceType.MCP, owner="acme")  # says nothing
    offer = _not_done("harbor-site")
    assert offer is not None
    assert offer["instance"] == "browser"
    assert offer["label"] == "Link your Browser (Playwright) to Harbor site and retry"
