"""Undo for data changes (services.undo, adapters.undo_sql, adapters.undo_services)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from ringkeeper.adapters import airtable, n8n
from ringkeeper.adapters.base import Target
from ringkeeper.adapters.undo_sql import CantUndoError, plan
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import DecisionStatus, Env, InstanceType
from ringkeeper.services import demo, undo
from ringkeeper.services.calls import approve, request_revert, run_call
from ringkeeper.vault import Secret, Vault, get_vault
from tests.fake_n8n import API_KEY as N8N_KEY
from tests.fake_services import AIRTABLE_TOKEN, FakeHttp

pytestmark = pytest.mark.anyio
ACME = Caller("acme-web", "session-1", "token-1")


def changes(project: str = "acme-web") -> list[dict[str, Any]]:
    with session_scope() as session:
        return undo.for_project(session, project)


def call(tool: str, instance: str, **args: object) -> ToolCall:
    return ToolCall(ACME, InstanceType.SUPABASE, tool, instance, args)


# --- Which statements can be undone ---


def test_plain_updates_and_deletes_can_be_undone() -> None:
    found = plan("UPDATE notes SET body = %s, tag = 'x' WHERE id = %s", ["new", 7])
    assert (found.kind, found.table, found.columns, found.where_params) == (
        "update",
        "notes",
        ["body", "tag"],
        [7],
    )
    found = plan("delete from public.notes where body like 'a%%'", None)
    assert (found.kind, found.table, found.where) == ("delete", "public.notes", "body like 'a%%'")
    # A WHERE inside quotes isn't the statement's.
    found = plan("UPDATE notes SET body = 'see where it goes'", None)
    assert found.where is None
    assert found.columns == ["body"]


@pytest.mark.parametrize(
    ("sql", "why"),
    [
        ("INSERT INTO notes (body) VALUES ('x')", "which rows an INSERT adds"),
        ("DROP TABLE notes", "tables themselves"),
        ("UPDATE notes SET body = n.body FROM other n WHERE n.id = notes.id", "one table"),
        ("DELETE FROM notes USING other WHERE other.id = notes.id", "one table"),
        ("WITH x AS (SELECT 1) UPDATE notes SET body = 'a'", "plain UPDATE or DELETE"),
        ("UPDATE notes SET (body, tag) = ('a', 'b')", "SET column = value"),
    ],
)
def test_other_writes_say_why_they_cant_be_undone(sql: str, why: str) -> None:
    with pytest.raises(CantUndoError, match=why):
        plan(sql, None)


def test_the_approval_card_says_whether_it_can_be_undone() -> None:
    assert undo.note(
        InstanceType.SUPABASE, "execute", {"sql": "DELETE FROM notes WHERE id = 1"}
    ) == {"possible": True, "why": None, "days": 7}
    note = undo.note(InstanceType.GMAIL, "send_email", {})
    assert note is not None
    assert note["possible"] is False
    assert "unsent" in str(note["why"])


# --- Postgres, end to end ---


def rows(dsn: str) -> list[tuple[object, ...]]:
    with psycopg.connect(dsn) as conn:
        return list(conn.execute("SELECT id, body FROM notes ORDER BY id"))


async def test_an_update_is_kept_and_reverted(
    registry: None, target_dsn: str, vault: Vault
) -> None:
    await run_call(
        call("execute", "acme-supabase-dev", sql="UPDATE notes SET body = 'changed' WHERE id = 1"),
        vault,
    )
    assert rows(target_dsn) == [(1, "changed")]
    [change] = changes()
    assert change["can_revert"] is True
    assert "1 row of notes" in change["restores"]

    decision = await request_revert(change["id"], vault)
    assert decision.status is DecisionStatus.PENDING  # a revert waits for approval too
    assert rows(target_dsn) == [(1, "changed")]
    done = await approve(str(decision.id), by="owner", vault=vault)
    assert done.status is DecisionStatus.EXECUTED, done.error
    assert rows(target_dsn) == [(1, "hello")]
    assert undo.find(change["id"]).reverted_at is not None
    with pytest.raises(RuleViolationError, match="already reverted"):
        await request_revert(change["id"], vault)


async def test_a_delete_is_put_back(registry: None, target_dsn: str, vault: Vault) -> None:
    await run_call(
        call("execute", "acme-supabase-dev", sql="DELETE FROM notes WHERE id = %s", params=[1]),
        vault,
    )
    assert rows(target_dsn) == []
    [change] = changes()
    decision = await request_revert(change["id"], vault)
    await approve(str(decision.id), by="owner", vault=vault)
    assert rows(target_dsn) == [(1, "hello")]


async def test_an_approved_production_change_is_kept(
    registry: None, target_dsn: str, vault: Vault
) -> None:
    held = await run_call(
        call("execute", "acme-supabase-prod", sql="UPDATE notes SET body = 'live'"), vault
    )
    await approve(str(held["decision_id"]), by="owner", vault=vault)
    [change] = changes()
    assert change["can_revert"] is True


async def test_a_revert_refuses_when_rows_changed_since_it_was_asked(
    registry: None, target_dsn: str, vault: Vault
) -> None:
    await run_call(
        call("execute", "acme-supabase-dev", sql="UPDATE notes SET body = 'a' WHERE id = 1"), vault
    )
    [change] = changes()
    decision = await request_revert(change["id"], vault)
    with psycopg.connect(target_dsn, autocommit=True) as conn:
        conn.execute("UPDATE notes SET body = 'someone else' WHERE id = 1")
    done = await approve(str(decision.id), by="owner", vault=vault)
    assert done.status is not DecisionStatus.EXECUTED
    assert rows(target_dsn) == [(1, "someone else")]


async def test_an_insert_runs_and_says_it_cant_be_undone(registry: None, vault: Vault) -> None:
    await run_call(
        call("execute", "acme-supabase-dev", sql="INSERT INTO notes (body) VALUES ('x')"), vault
    )
    [change] = changes()
    assert change["can_revert"] is False
    assert "INSERT" in change["why_not"]


async def test_kept_changes_expire_after_seven_days(registry: None, vault: Vault) -> None:
    await run_call(call("execute", "acme-supabase-dev", sql="UPDATE notes SET body = 'b'"), vault)
    assert undo.expire(datetime.now(UTC) + timedelta(days=6)) == 0
    assert undo.expire(datetime.now(UTC) + timedelta(days=7, minutes=1)) == 1
    assert changes() == []  # type: ignore[list-item]


async def test_a_full_undo_space_says_so(
    registry: None, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(undo, "PROJECT_CAP_BYTES", 10)
    await run_call(call("execute", "acme-supabase-dev", sql="UPDATE notes SET body = 'c'"), vault)
    [change] = changes()
    assert change["can_revert"] is False
    assert "undo space" in change["why_not"]


# --- The demo's SQLite database ---


async def test_the_demo_database_reverts_too(
    database_url: str, data_dir: Path, tmp_path: Path
) -> None:
    settings = Settings(mode="desktop", data_dir=data_dir, workspaces_root=str(tmp_path / "work"))
    with session_scope() as session:
        made = demo.create(session, settings, get_vault())
    caller = Caller(demo.PROJECT, "s-1", "t-1")
    update = ToolCall(caller, InstanceType.SUPABASE, "execute", demo.CONNECTION,
                      {"sql": "UPDATE orders SET status = 'shipped' WHERE id = 1007"})  # fmt: skip
    held = await run_call(update, get_vault())
    await approve(str(held["decision_id"]), by="owner", vault=get_vault())
    [change] = changes(demo.PROJECT)
    assert "status" in str(change["restores"])
    decision = await request_revert(change["id"], get_vault())
    await approve(str(decision.id), by="owner", vault=get_vault())
    import sqlite3  # noqa: PLC0415

    status = (
        sqlite3.connect(made.database)
        .execute("SELECT status FROM orders WHERE id = 1007")
        .fetchone()
    )
    assert status == ("paid",)


# --- Airtable and n8n (fakes) ---

BASE = "app" + "A" * 14
RECORD = "rec" + "B" * 14


def at_target() -> Target:
    return Target("acme-at", Env.DEV, None, Secret("file://acme/at", AIRTABLE_TOKEN))


async def test_airtable_update_keeps_only_the_fields_it_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = FakeHttp()
    monkeypatch.setattr(airtable, "transport", http.transport())
    http.routes[("GET", f"/v0/{BASE}/Leads/{RECORD}")] = {
        "id": RECORD,
        "fields": {"Name": "Ann", "Stage": "new", "Notes": "long text"},
    }
    spec = airtable.update_records
    assert spec.undo is not None
    change = {"id": RECORD, "fields": {"Stage": "won"}}
    kept = await spec.undo.before(
        at_target(), {"base_id": BASE, "table": "Leads", "records": [change]}
    )
    assert kept["records"] == [{"id": RECORD, "fields": {"Stage": "new"}}]
    http.routes[("PATCH", f"/v0/{BASE}/Leads")] = {"records": [{"id": RECORD}]}
    await spec.undo.restore(at_target(), kept)
    assert json.loads(http.requests[-1].content) == {
        "records": [{"id": RECORD, "fields": {"Stage": "new"}}]
    }


async def test_n8n_workflow_comes_back_as_it_was(fake_n8n: object) -> None:
    spec = n8n.update_workflow
    assert spec.undo is not None
    target = Target(
        "acme-n8n", Env.PROD, "https://n8n.acme.example", Secret("file://acme/n8n", N8N_KEY)
    )
    kept = await spec.undo.before(
        target, {"workflow_id": "wf1", "name": "x", "nodes": [], "connections": {}}
    )
    assert kept["workflow"]["name"] == "Sync leads"
    assert "Sync leads" in spec.undo.describe(kept)


async def test_a_change_is_listed_on_the_task_that_made_it(
    registry: None, target_dsn: str, vault: Vault
) -> None:
    from ringkeeper.services import tasks  # noqa: PLC0415

    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="acme-web", prompt="tidy notes")
        task_id, worker = str(task.id), str(tasks.latest_session(task).id)
    caller = Caller("acme-web", worker, "token-1")
    sql = "UPDATE notes SET body = 'tidied' WHERE id = 1"
    await run_call(
        ToolCall(caller, InstanceType.SUPABASE, "execute", "acme-supabase-dev", {"sql": sql}), vault
    )
    with session_scope() as session:
        [listed] = undo.for_tasks(session, [task_id])
    assert listed["what"] == sql
    assert listed["can_revert"] is True
