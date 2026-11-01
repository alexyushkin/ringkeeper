"""Agents and routines: an agent reaches only its connections and works with its
instructions, model and autonomy; a routine runs on its schedule (once after the
computer slept), once per event, within its limits, holds its writes for approval
while nobody watches, and starts paused with a test run."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper.api.app import create_app
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    Decision,
    DecisionKind,
    InstanceType,
    Routine,
    RoutineRun,
    Task,
    WorkerSession,
)
from ringkeeper.services import agents, routines, runtime
from ringkeeper.services.agents import AgentIn
from ringkeeper.services.calls import RefusalError, bound_instances, run_call
from ringkeeper.services.routines import RoutineIn
from ringkeeper.vault import get_vault
from tests import test_web_api
from tests.conftest import CLAUDE_API_KEY
from tests.helpers import bind, make_instance, make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

UTC_ZONE = "UTC"


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


@pytest.fixture
def seaside(database_url: str, tmp_path: Path) -> Path:
    """A project with three connections: a database, an SMS account, a shared tool."""
    folder = tmp_path / "seaside"
    folder.mkdir()
    get_vault().store("file://seaside/sms", json.dumps({"sid": "AC1", "token": "t"}))
    get_vault().store(get_settings().claude_api_key_ref, CLAUDE_API_KEY)
    with session_scope() as session:
        make_project(session, "seaside", owner="seaside", workspace_path=str(folder))
        make_instance(session, "seaside-db")
        make_instance(session, "seaside-sms", type=InstanceType.TWILIO,
                      secret_ref="file://seaside/sms")  # fmt: skip
        make_instance(session, "browser", type=InstanceType.MCP, shared=True, owner="you")
    for name in ("seaside-db", "seaside-sms", "browser"):
        bind("seaside", name)
    with session_scope() as session:
        agents.create_agent(
            session, "seaside",
            AgentIn("Follow-up", "Be brief. Sign as Seaside.", ("seaside-sms",),
                    model="claude-haiku-5-5", effort="low", autonomy="strict"),
        )  # fmt: skip
    return folder


def routine(**fields: Any) -> str:
    base: dict[str, Any] = {
        "name": "Digest", "agent": "follow-up", "prompt": "Digest for {{today}}",
        "trigger": "schedule", "schedule": {"every": "hours", "hours": 1}, **fields,
    }  # fmt: skip
    data = RoutineIn(**base)
    with session_scope() as session:
        made = routines.create_routine(session, "seaside", data, tz=UTC_ZONE)
        return str(made.id)


def turn_on(ident: str, now: datetime) -> None:
    with session_scope() as session:
        routines.set_paused(session, ident, False, now=now)


def tick(now: datetime) -> list[routines.Started]:
    with session_scope() as session:
        started = routines.tick(session, get_settings(), now)
        session.flush()
        for s in started:
            _ = s.run.skipped, s.run.missed, s.run.trigger, s.run.task_id, s.run.event
        session.expunge_all()
        return started


def runs(ident: str) -> list[RoutineRun]:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(RoutineRun).where(RoutineRun.routine_id == routines.get_routine(
                    session, ident).id).order_by(RoutineRun.created_at)
            )
        )  # fmt: skip
        session.expunge_all()
        return rows


# --- Agents ---


def test_an_agent_s_connections_are_some_of_its_project_s(seaside: Path) -> None:
    with session_scope() as session, pytest.raises(RuleViolationError, match="isn't linked"):
        agents.create_agent(session, "seaside", AgentIn("x", connections=("elsewhere",)))


def test_an_agent_reaches_only_its_connections(seaside: Path) -> None:
    ident = routine()
    with session_scope() as session:
        started = routines.start_run(session, get_settings(), routines.get_routine(session, ident),
                                     "manual")  # fmt: skip
        assert started.task is not None
        worker = session.scalars(
            select(WorkerSession).where(WorkerSession.task_id == started.task.id)
        ).one()
        session_id = str(worker.id)
    caller = Caller("seaside", session_id, "token")
    assert [i["name"] for i in bound_instances(caller)] == ["seaside-sms"]
    with pytest.raises(RefusalError, match="isn't one of the follow-up agent's connections"):
        asyncio.run(
            run_call(
                ToolCall(caller, InstanceType.SUPABASE, "query", "seaside-db", {"sql": "SELECT 1"}),
                get_vault(),
            )
        )
    # A task not run as an agent still has every connection of the project.
    others = Caller("seaside", "00000000-0000-0000-0000-000000000000", "token")
    assert {i["name"] for i in bound_instances(others)} == {"seaside-db", "seaside-sms", "browser"}


def test_an_unattended_run_holds_its_writes_for_approval(seaside: Path) -> None:
    ident = routine()
    with session_scope() as session:
        started = routines.start_run(session, get_settings(), routines.get_routine(session, ident),
                                     "schedule")  # fmt: skip
        assert started.task is not None
        worker = session.scalars(
            select(WorkerSession).where(WorkerSession.task_id == started.task.id)
        ).one()
        caller = Caller("seaside", str(worker.id), "token")
    args = {"to": "+15550100", "from_number": "+15550199", "body": "Thanks for your call!"}
    held = asyncio.run(
        run_call(
            ToolCall(caller, InstanceType.TWILIO, "send_sms", "seaside-sms", args), get_vault()
        )
    )
    assert held["status"] == "held_for_approval"  # waits in Approvals: nobody is watching
    with session_scope() as session:
        decision = session.scalars(select(Decision)).one()
        assert decision.kind is DecisionKind.APPROVE


def test_the_agent_s_instructions_model_and_autonomy_reach_the_worker(seaside: Path) -> None:
    from ringkeeper.workers.manager import Manager  # noqa: PLC0415
    from tests.fake_runtime import FakeRuntime  # noqa: PLC0415

    ident = routine()
    with session_scope() as session:
        routines.start_run(session, get_settings(), routines.get_routine(session, ident), "manual")
    fake = FakeRuntime(None)
    manager = Manager(fake, get_vault())
    manager.tick()
    manager.wait_idle()
    task = json.loads(fake.started[0].env["RINGKEEPER_TASK"])
    assert task["prompt"].startswith("You are the follow-up agent for this project.")
    assert "Be brief. Sign as Seaside." in task["prompt"]
    assert "Digest for " in task["prompt"]
    assert (task["model"], task["effort"]) == ("claude-haiku-5-5", "low")
    assert task["max_turns"] == 40  # the routine's steps per run
    assert list(task["mcp_servers"]) == ["ringkeeper-twilio"]  # only its connections' services


# --- Routines ---


def test_a_new_routine_starts_paused_and_a_test_run_still_runs(seaside: Path) -> None:
    ident = routine()
    assert tick(at("2026-11-02T10:00:00")) == []  # paused: nothing runs on its own
    with session_scope() as session:
        found = routines.get_routine(session, ident)
        assert found.paused
        test = routines.start_run(session, get_settings(), found, "test")
        assert test.task is not None
        assert test.run.trigger == "test"


def test_a_schedule_fires_on_time(seaside: Path) -> None:
    ident = routine()
    turn_on(ident, at("2026-11-02T09:10:00"))
    assert tick(at("2026-11-02T09:30:00")) == []  # not 10:00 yet
    fired = tick(at("2026-11-02T10:00:05"))
    assert [(s.run.trigger, s.run.missed) for s in fired] == [("schedule", 0)]
    assert tick(at("2026-11-02T10:00:35")) == []  # once
    with session_scope() as session:
        task = session.scalars(select(Task)).one()
        assert task.prompt == "Digest for 2026-11-02"
        assert task.max_runtime_s == 15 * 60


def test_after_sleep_one_catch_up_run_says_what_it_makes_up_for(seaside: Path) -> None:
    ident = routine()
    turn_on(ident, at("2026-11-02T09:10:00"))
    fired = tick(at("2026-11-02T13:20:00"))  # asleep through 10, 11, 12 and 13
    assert [(s.run.trigger, s.run.missed) for s in fired] == [("catch_up", 3)]
    assert tick(at("2026-11-02T13:21:00")) == []
    with session_scope() as session:
        task = session.scalars(select(Task)).one()
        assert "makes up for 3 missed runs" in task.prompt
        assert routines.get_routine(session, ident).next_run_at == at("2026-11-02T14:00:00")


def test_catching_up_can_be_turned_off(seaside: Path) -> None:
    ident = routine(catch_up=False)
    turn_on(ident, at("2026-11-02T09:10:00"))
    [skipped] = tick(at("2026-11-02T13:20:00"))
    assert skipped.run.task_id is None
    assert skipped.run.skipped is not None
    assert "catching up is off" in skipped.run.skipped


def test_the_day_s_limit_stops_runs(seaside: Path) -> None:
    ident = routine(max_runs_per_day=2)
    with session_scope() as session:
        found = routines.get_routine(session, ident)
        now = at("2026-11-02T10:00:00")
        first, second, third = (
            routines.start_run(
                session, get_settings(), found, "manual", now=now + timedelta(minutes=i)
            )
            for i in range(3)
        )
        assert first.task is not None
        assert second.task is not None
        assert third.task is None
        assert third.run.skipped == "today's limit of 2 runs was reached"
        tomorrow = routines.start_run(
            session, get_settings(), found, "manual", now=at("2026-11-03T08:00:00")
        )
        assert tomorrow.task is not None


def test_a_dollar_cap_in_api_key_mode_and_steps_and_minutes_always(seaside: Path) -> None:
    ident = routine(budget_usd=0.5, max_steps=12, max_minutes=5)
    with session_scope() as session:
        started = routines.start_run(
            session, get_settings(), routines.get_routine(session, ident), "manual"
        )
        assert started.task is not None
        assert (started.task.budget_usd, started.task.max_turns, started.task.max_runtime_s) == (
            0.5, 12, 300,
        )  # fmt: skip
        runtime.update(session, {"claude_auth_mode": "subscription"}, by="owner")
    with session_scope() as session:
        again = routines.start_run(
            session, get_settings(), routines.get_routine(session, ident), "manual"
        )
        assert again.task is not None
        assert (again.task.budget_usd, again.task.max_turns) == (None, 12)  # no $ cap there


def test_an_event_fires_once_per_event(seaside: Path) -> None:
    ident = routine(
        trigger="event", event={"source": "webhook"}, schedule=None, prompt="Lead: {{event}}"
    )
    turn_on(ident, at("2026-11-02T09:00:00"))
    with session_scope() as session:
        secret = routines.get_routine(session, ident).hook_secret
        assert secret
        body = {"name": "Anna", "email": "anna@example.com"}
        first = routines.hook(session, get_settings(), ident, secret, body, "lead-1")
        again = routines.hook(session, get_settings(), ident, secret, body, "lead-1")
        other = routines.hook(session, get_settings(), ident, secret, body, "lead-2")
        assert first.run.id == again.run.id  # the same event: one run
        assert first.task is not None
        assert other.task is not None
        assert "name: Anna" in first.task.prompt
    assert len(runs(ident)) == 2


def test_a_file_added_to_the_folder_starts_one_run(seaside: Path) -> None:
    (seaside / "already-here.txt").write_text("x")
    ident = routine(trigger="event", event={"source": "folder"}, schedule=None,
                    prompt="New file: {{event.file}}")  # fmt: skip
    turn_on(ident, at("2026-11-02T09:00:00"))
    assert tick(at("2026-11-02T09:00:30")) == []  # the first look only notes what's there
    (seaside / "invoices").mkdir()
    (seaside / "invoices" / "march.pdf").write_text("pdf")
    (seaside / ".git").mkdir()
    (seaside / ".git" / "index").write_text("not the owner's")
    fired = tick(at("2026-11-02T09:01:00"))
    assert [s.run.event for s in fired] == [{"file": "invoices/march.pdf"}]
    assert tick(at("2026-11-02T09:01:30")) == []  # once per file
    with session_scope() as session:
        assert session.scalars(select(Task)).one().prompt == "New file: invoices/march.pdf"


def test_pausing_stops_runs_at_every_level(seaside: Path) -> None:
    ident = routine()
    turn_on(ident, at("2026-11-02T09:10:00"))
    with session_scope() as session:
        runtime.update(session, {"routines_paused": True}, by="owner")  # the tray
    assert tick(at("2026-11-02T10:00:05")) == []
    with session_scope() as session:
        runtime.update(session, {"routines_paused": False}, by="owner")
        routines.get_routine(session, ident).project.paused = True  # the project
    assert tick(at("2026-11-02T11:00:05")) == []
    with session_scope() as session:
        routines.get_routine(session, ident).project.paused = False
    assert [s.run.trigger for s in tick(at("2026-11-02T12:00:05"))] == ["catch_up"]
    with session_scope() as session:
        assert session.scalars(select(Routine)).one().paused is False


# --- Through the API ---


def test_agents_and_routines_through_the_api(owner: TestClient, tmp_path: Path) -> None:
    get_vault().store(get_settings().claude_api_key_ref, CLAUDE_API_KEY)
    assert owner.post("/projects", json={"name": "seaside", "owner": "Seaside"}).status_code == 201
    made = owner.post(
        "/connections",
        json={"name": "seaside-n8n", "type": "n8n", "env": "dev", "owner": "seaside",
              "secret": "k", "base_url": "https://n8n.example.com"},
    )  # fmt: skip
    assert made.status_code == 201, made.text
    assert owner.put("/projects/seaside/connections/seaside-n8n", json={}).status_code == 200
    agent = owner.post(
        "/projects/seaside/agents",
        json={"name": "Follow-up", "instructions": "Be brief.", "connections": ["seaside-n8n"]},
    )
    assert agent.status_code == 201, agent.text
    assert agent.json()["name"] == "follow-up"

    template = owner.get("/routine-templates").json()["daily-digest"]
    created = owner.post(
        "/projects/seaside/routines",
        json={**template, "agent": "follow-up", "tz": "America/New_York"},
    )
    assert created.status_code == 201, created.text
    routine = created.json()
    assert routine["paused"] is True  # test it first
    assert routine["when"] == "Weekdays at 8:00 AM (America/New_York)"

    tested = owner.post(f"/routines/{routine['id']}/run", json={"test": True}).json()
    assert tested["task_id"] is not None
    [run] = owner.get(f"/routines/{routine['id']}/runs").json()
    assert (run["trigger"], run["status"]) == ("test", "queued")

    on = owner.post(f"/routines/{routine['id']}/paused", json={"paused": False}).json()
    assert on["paused"] is False
    assert on["next_run_at"] is not None

    lead = owner.post(
        "/projects/seaside/routines",
        json={**owner.get("/routine-templates").json()["lead-follow-up"], "agent": "follow-up"},
    ).json()
    owner.post(f"/routines/{lead['id']}/paused", json={"paused": False})
    hook = lead["hook_path"]
    assert hook.startswith(f"/hooks/{lead['id']}/")
    with TestClient(create_app()) as outside:  # no sign-in: the secret is the check
        called = outside.post(hook, json={"name": "Anna"}, headers={"Idempotency-Key": "a1"})
        assert called.status_code == 202, called.text
        assert called.json()["started"] is True
        again = outside.post(hook, json={"name": "Anna"}, headers={"Idempotency-Key": "a1"})
        assert again.json()["run"] == called.json()["run"]
        wrong = outside.post(f"/hooks/{lead['id']}/not-the-secret", json={})
        assert wrong.status_code == 404


def test_a_new_airtable_record_starts_one_run(
    seaside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ringkeeper.services import routine_events  # noqa: PLC0415

    get_vault().store("file://seaside/airtable", "pat-not-real")
    with session_scope() as session:
        make_instance(session, "seaside-airtable", type=InstanceType.AIRTABLE,
                      secret_ref="file://seaside/airtable")  # fmt: skip
    bind("seaside", "seaside-airtable")
    records: list[tuple[str, dict[str, Any]]] = [("rec1", {"Name": "Old lead"})]

    async def fake(_target: Any, event: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        assert event["table"] == "Leads"
        return list(records)

    monkeypatch.setitem(routine_events.POLLERS, "airtable", fake)
    event = {
        "source": "airtable",
        "connection": "seaside-airtable",
        "base": "appX",
        "table": "Leads",
    }
    ident = routine(trigger="event", event=event, schedule=None, prompt="Follow up {{event.Name}}")
    turn_on(ident, at("2026-11-02T09:00:00"))
    assert tick(at("2026-11-02T09:00:30")) == []  # the first look only notes what's there
    records.append(("rec2", {"Name": "Anna"}))
    assert tick(at("2026-11-02T09:02:00")) == []  # checked every 5 minutes
    fired = tick(at("2026-11-02T09:06:00"))
    assert [s.run.event for s in fired] == [{"Name": "Anna"}]
    assert tick(at("2026-11-02T09:12:00")) == []  # once per record
    with session_scope() as session:
        assert session.scalars(select(Task)).one().prompt == "Follow up Anna"
    with session_scope() as session, pytest.raises(RuleViolationError, match="Airtable"):
        routines.create_routine(
            session, "seaside",
            RoutineIn("x", "follow-up", "p", "event", event={**event, "connection": "seaside-db"}),
        )  # fmt: skip


def test_agents_and_routines_across_projects_follow_the_switcher(owner: TestClient) -> None:
    for name, client in (("seaside", "Seaside"), ("acme-web", "Acme")):
        made = owner.post("/projects", json={"name": name, "owner": client})
        assert made.status_code == 201, made.text
        added = owner.post(f"/projects/{name}/agents", json={"name": "digest"})
        assert added.status_code == 201, added.text
    template = owner.get("/routine-templates").json()["daily-digest"]
    made = owner.post("/projects/seaside/routines", json={**template, "agent": "digest"})
    assert made.status_code == 201, made.text

    everyone = owner.get("/agents").json()
    assert [(a["project"], a["client"], a["name"], a["routines"]) for a in everyone] == [
        ("acme-web", "acme", "digest", 0),
        ("seaside", "seaside", "digest", 1),
    ]
    assert [a["project"] for a in owner.get("/agents", params={"client": "seaside"}).json()] == [
        "seaside"
    ]
    assert [a["project"] for a in owner.get("/agents", params={"project": "acme-web"}).json()] == [
        "acme-web"
    ]
    assert owner.get("/routines", params={"client": "acme"}).json() == []
    assert len(owner.get("/routines", params={"client": "seaside"}).json()) == 1
