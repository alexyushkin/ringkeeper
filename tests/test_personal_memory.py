"""The owner's own Notion memory, against a fake Notion MCP server: what runs, what's refused, what
stays held, and that a client's knowledge stays in its project."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper.adapters import mcp
from ringkeeper.adapters.base import ToolError
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import AuditEvent, AuditOutcome, Env, InstanceType, Project
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import (
    bindings,
    instances,
    mcp_tools,
    memory,
    personal_memory,
    projects,
    secrets_in,
)
from ringkeeper.services.calls import RefusalError, run_call
from ringkeeper.vault import get_vault
from tests import test_web_api
from tests.helpers import make_project
from tests.test_memory import worker

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner
SERVER = Path(__file__).parent / "fixtures" / "fake_notion_mcp.py"
MINE = "my-notion"
THEIRS = "acme-notion-mcp"
LIKES = {"parent": "memory",
         "pages": [{"properties": {"title": "Short emails", "Type": "Preference"},
                    "content": "I like short emails with one clear ask."}]}  # fmt: skip
TOOLS = ("notion-memory-search", "notion-fetch", "notion-create-pages", "notion-delete-pages",
         "notion-update-page")  # fmt: skip


def add(name: str, client: str, saved: Path, *, shared: bool) -> None:
    secret = json.dumps({
        "command": sys.executable, "args": [str(SERVER)],
        "env": {"FAKE_NOTION_SAVED": str(saved)},
    })  # fmt: skip
    with session_scope() as session:
        ref = secrets_in.store_connection(session, get_settings(), name, InstanceType.MCP, secret)
        instances.create_instance(session, InstanceIn(
            name=name, title=name, type=InstanceType.MCP, env=Env.DEV, secret_ref=ref, owner=client,
            config={"remote": "notion"}, shared=shared,
        ))  # fmt: skip


@pytest.fixture
def saved(database_url: str, tmp_path: Path) -> Path:
    file = tmp_path / "saved.json"
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme", title="Harbor Rebuild",
                     aliases=["the marina job"])  # fmt: skip
        make_project(session, "globex-app", owner="globex")
    add(MINE, "you", file, shared=True)
    add(THEIRS, "acme", file, shared=False)
    with session_scope() as session:
        for project, name in (("acme-web", MINE), ("globex-app", MINE), ("acme-web", THEIRS)):
            bindings.set_binding(session, BindingIn(project=project, instance=name))
        personal_memory.set_enabled(session, MINE, True)
    return file


def call(tool: str, arguments: dict[str, Any], instance: str = MINE) -> Any:
    args = {"tool": tool, "arguments": arguments}
    caller = Caller("acme-web", "session-1", "token-1")
    return asyncio.run(
        run_call(ToolCall(caller, InstanceType.MCP, "call_tool", instance, args), get_vault())
    )


def saves(file: Path) -> int:
    return len(json.loads(file.read_text())) if file.exists() else 0


def with_text(text: str, kind: str = "Preference") -> dict[str, Any]:
    page = {"properties": {"title": "A note", "Type": kind}, "content": text}
    return {"parent": "memory", "pages": [page]}


# --- What runs ---


def test_searching_and_reading_memory_run_without_asking(saved: Path) -> None:
    assert "Prefers short emails" in call("notion-memory-search", {"query": "email"})["text"]
    assert "About me" in call("notion-fetch", {"id": "memory"})["text"]
    assert call("notion-fetch", {"id": "notion://docs/memory"})["text"]


def test_a_save_about_the_owner_runs_without_asking(saved: Path) -> None:
    answer = call("notion-create-pages", LIKES)
    assert answer.get("status") != "held_for_approval"
    assert saves(saved) == 1


def test_a_save_is_in_the_activity_log(saved: Path) -> None:
    call("notion-create-pages", LIKES)
    with session_scope() as session:
        done = session.scalars(
            select(AuditEvent).where(AuditEvent.outcome == AuditOutcome.EXECUTED)
        ).all()
    assert [e.instance for e in done] == [MINE]


def test_a_search_isnt_logged_as_a_change(saved: Path) -> None:
    call("notion-memory-search", {"query": "x"})
    with session_scope() as session:
        query = select(AuditEvent).where(AuditEvent.outcome == AuditOutcome.EXECUTED)
        assert session.scalars(query).all() == []


def test_creating_any_other_page_still_waits(saved: Path) -> None:
    answer = call("notion-create-pages", {"parent": {"page_id": "abc"}, "pages": LIKES["pages"]})
    assert answer["status"] == "held_for_approval"
    assert saves(saved) == 0


def test_the_rules_say_it_too() -> None:
    config = {"remote": "notion", "personal_memory": True}
    assert mcp.call_rule(config, "notion-memory-search", {"query": "x"}).level == mcp.READ
    assert mcp.call_rule(config, "notion-create-pages", LIKES).level == mcp.READ
    assert mcp.call_rule(config, "notion-fetch", {"id": "memory"}).level == mcp.READ


def test_a_stricter_choice_of_the_owners_wins() -> None:
    config = {"remote": "notion", "personal_memory": True,
              "tool_rules": {"notion-create-pages": mcp.APPROVE}}  # fmt: skip
    assert mcp.call_rule(config, "notion-create-pages", LIKES).level == mcp.APPROVE


# --- What stays held ---


def test_deleting_a_memory_waits_for_the_name_typed(saved: Path) -> None:
    mcp_tools.refresh(MINE)
    answer = call("notion-delete-pages", {"page_ids": ["m1"], "user_confirmed": True})
    assert answer["status"] == "held_for_approval"
    with session_scope() as session:
        config = instances.get_instance(session, MINE).config
    assert mcp.call_rule(config, "notion-delete-pages", {}).level == mcp.CONFIRM


def test_editing_a_memory_is_an_ordinary_held_edit(saved: Path) -> None:
    mcp_tools.refresh(MINE)
    answer = call("notion-update-page", {"page_id": "m1", "command": "replace_content"})
    assert answer["status"] == "held_for_approval"


# --- What's refused ---


@pytest.mark.parametrize(
    "text",
    [
        "Acme prefers invoices monthly",  # a client
        "On acme-web, deploy on Fridays",  # a project's short name
        "acme web uses a staging site",  # the same, with a space
        "Harbor Rebuild runs late",  # a project's full display name
        "Globex wants weekly calls",  # another client altogether
    ],
)
def test_a_save_naming_a_client_or_project_is_refused(saved: Path, text: str) -> None:
    with pytest.raises((ToolError, RefusalError), match="only for the owner's own"):
        call("notion-create-pages", with_text(text))
    assert saves(saved) == 0


@pytest.mark.parametrize(
    "text",
    ["My token is ghp_0123456789abcdefghijklmnopqrstuvwxyz", "password: hunter2hunter2"],
)  # fmt: skip
def test_a_save_with_a_secret_or_personal_details_is_refused(saved: Path, text: str) -> None:
    with pytest.raises((ToolError, RefusalError)):
        call("notion-create-pages", with_text(text))
    assert saves(saved) == 0


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("Write to pat@elsewhere.example first", "pat@elsewhere.example"),
        ("Text me on 415-555-0132", "415-555-0132"),
        ("Post it to 12 Baker Street", "12 Baker Street"),
    ],
)
def test_a_save_goes_without_personal_details(saved: Path, text: str, gone: str) -> None:
    call("notion-create-pages", with_text(text))
    assert gone not in saved.read_text()
    assert "[removed]" in saved.read_text()


@pytest.mark.parametrize(
    "text",
    ["Ship on 2026-11-06 at 10:00", "Use version 1.24.3", "Allow 10 minutes on the way",
     "Budget is 1,200 a month", "Room 12 is fine", "Keep it under 300 words"],
)  # fmt: skip
def test_ordinary_numbers_stay(text: str) -> None:
    assert personal_memory.take_out(text) == (text, [])


@pytest.mark.parametrize(
    "text",
    ["(415) 555-0132", "+1 415 555 0132", "415.555.0132", "+44 20 7946 0958", "+14155550132",
     "phone: 4155550132", "1200 Market Street", "5 Fifth Ave", "PO Box 123", "9 Elm Rd"],
)  # fmt: skip
def test_phone_numbers_and_street_addresses_go(text: str) -> None:
    cleaned, found = personal_memory.take_out(f"Use {text} please")
    assert found
    assert text not in cleaned


def test_a_single_common_word_never_blocks_a_save(saved: Path) -> None:
    with session_scope() as session:
        make_project(session, "site", owner="acme", title="Website")
        make_project(session, "mobile", owner="acme", title="App")
    assert call("notion-create-pages", with_text("I like my website copy short"))
    assert call("notion-create-pages", with_text("Build the app before the docs"))
    assert saves(saved) == 2


def test_only_the_whole_name_of_a_project_counts(saved: Path) -> None:
    assert call("notion-create-pages", with_text("A harbor view rebuild is a nice phrase"))
    assert saves(saved) == 1
    with pytest.raises((ToolError, RefusalError), match="harbor rebuild"):
        call("notion-create-pages", with_text("harbor  rebuild notes"))


def test_a_company_suffix_isnt_needed_to_match(saved: Path) -> None:
    with session_scope() as session:
        make_project(session, "bright-web", owner="Bright Homes LLC", title="Roofing Quotes")
    with pytest.raises((ToolError, RefusalError), match="bright homes"):
        call("notion-create-pages", with_text("Bright Homes wants weekly calls"))


def test_a_save_about_other_people_is_refused(saved: Path) -> None:
    with pytest.raises((ToolError, RefusalError), match="Type"):
        call("notion-create-pages", with_text("Sam prefers mornings", "People"))
    assert saves(saved) == 0


def test_the_name_hidden_in_a_title_is_found_too(saved: Path) -> None:
    arguments = {"parent": "memory", "pages": [
        {"properties": {"title": "Globex notes", "Type": "Notes"}, "content": "Nothing here"},
    ]}  # fmt: skip
    with pytest.raises((ToolError, RefusalError), match="globex"):
        call("notion-create-pages", arguments)


def test_a_word_inside_another_word_doesnt_count(saved: Path) -> None:
    assert call("notion-create-pages", with_text("I like an acmeistic style"))
    assert saves(saved) == 1


def test_a_refusal_is_in_the_activity_log(saved: Path) -> None:
    with pytest.raises((ToolError, RefusalError)):
        call("notion-create-pages", with_text("Acme prefers invoices monthly"))
    with session_scope() as session:
        refused = session.scalars(
            select(AuditEvent).where(AuditEvent.outcome == AuditOutcome.REFUSED)
        ).all()
    assert refused


# --- Only the owner's own ---


def test_a_clients_connection_cant_turn_it_on(saved: Path) -> None:
    with session_scope() as session, pytest.raises(RuleViolationError, match="not a client's"):
        personal_memory.set_enabled(session, THEIRS, True)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [("notion-memory-search", {"query": "x"}), ("notion-fetch", {"id": "memory"}),
     ("notion-create-pages", LIKES)],
)  # fmt: skip
def test_memory_calls_are_refused_where_its_not_on(
    saved: Path, tool: str, arguments: dict[str, Any]
) -> None:
    with pytest.raises((ToolError, RefusalError), match="isn't on"):
        call(tool, arguments, THEIRS)
    assert saves(saved) == 0


def test_a_flag_set_on_a_clients_connection_by_hand_still_doesnt_count(saved: Path) -> None:
    with session_scope() as session:
        found = instances.get_instance(session, THEIRS)
        found.config = {**found.config, "personal_memory": True}
    with pytest.raises((ToolError, RefusalError), match="isn't on"):
        call("notion-memory-search", {"query": "x"}, THEIRS)


def test_turned_off_it_stops(saved: Path) -> None:
    with session_scope() as session:
        personal_memory.set_enabled(session, MINE, False)
    with pytest.raises((ToolError, RefusalError), match="isn't on"):
        call("notion-create-pages", LIKES)


# --- Project memory stays apart ---


def test_a_clients_knowledge_goes_in_its_project_and_stays_there(saved: Path) -> None:
    with pytest.raises((ToolError, RefusalError)):
        call("notion-create-pages", with_text("Acme prefers invoices monthly"))
    assert memory.remember(worker("acme-web"), "Acme prefers invoices monthly")["kept"]
    found = memory.recall(worker("acme-web"))["notes"]
    assert [n["note"] for n in found] == ["Acme prefers invoices monthly"]
    assert memory.recall(worker("globex-app"))["notes"] == []
    assert saves(saved) == 0


# --- What a worker is told ---


def told(project: str) -> str | None:
    with session_scope(commit=False) as session:
        return personal_memory.instructions(projects.get_project(session, project), None)


def test_a_worker_is_told_to_search_first_and_save_at_the_end(saved: Path) -> None:
    said = told("acme-web")
    assert said is not None
    assert "notion://docs/memory" in said
    assert "notion-memory-search" in said
    assert 'parent "memory"' in said
    assert "Never put anything about a client or project there" in said


def test_nothing_is_said_when_its_off(saved: Path) -> None:
    with session_scope() as session:
        personal_memory.set_enabled(session, MINE, False)
    assert told("acme-web") is None


def test_every_worker_hears_where_client_knowledge_goes() -> None:
    from ringkeeper.gateway.memory import build_memory_server  # noqa: PLC0415

    server = build_memory_server(lambda: Caller("p", "s", "t"))
    assert "never in My memory (mine=true)" in (server.instructions or "")


# --- Settings, and a refreshed tool list ---


def test_refreshing_shows_notions_new_tools(saved: Path) -> None:
    listed = mcp_tools.refresh(MINE)
    assert {t["name"] for t in listed["tools"]} >= set(TOOLS)
    with session_scope() as session:
        shown = personal_memory.status(instances.get_instance(session, MINE))
    assert shown["offered"] is True
    assert shown["hint"] is None


def test_a_hint_when_notion_doesnt_offer_memory(saved: Path) -> None:
    with session_scope() as session:
        found = instances.get_instance(session, MINE)
        found.config = {**found.config, "annotations": {"notion-search": {"read_only": True}}}
        shown = personal_memory.status(found)
    assert shown["offered"] is False
    assert "Use memories" in shown["hint"]
    assert "Business and Enterprise" in shown["hint"]


def test_settings_through_the_api(owner: TestClient, saved: Path) -> None:
    listed = owner.get("/memory/personal").json()
    assert [c["name"] for c in listed["connections"]] == [MINE]  # not the client's
    off = owner.put("/memory/personal", json={"connection": MINE, "enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["connections"][0]["enabled"] is False
    refused = owner.put("/memory/personal", json={"connection": THEIRS, "enabled": True})
    assert refused.status_code in (409, 422)
    with session_scope() as session:
        assert session.get(Project, projects.get_project(session, "acme-web").id) is not None
