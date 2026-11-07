"""Automation watch: a failed n8n run, Vercel deploy or GitHub check, a new issue, or a website
that stops answering starts one run of a routine. Services are read through the gateway, as
the routine's project, so another client's connection is refused there."""

from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
from sqlalchemy import select

from ringkeeper.adapters import n8n
from ringkeeper.adapters.base import JSON
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    AuditEvent,
    AuditOutcome,
    Instance,
    InstanceType,
    Project,
    ProjectInstance,
    RoutineRun,
    Task,
)
from ringkeeper.services import agents, routine_events, routines
from ringkeeper.services.agents import AgentIn
from ringkeeper.services.routines import TEMPLATES, RoutineIn
from ringkeeper.vault import get_vault
from tests.conftest import CLAUDE_API_KEY
from tests.fake_n8n import API_KEY, FakeN8n
from tests.helpers import bind, make_instance, make_project

ACME, GLOBEX = "acme-web", "globex-portal"


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


@pytest.fixture
def fake(database_url: str, monkeypatch: pytest.MonkeyPatch) -> FakeN8n:
    """Acme's project with its n8n, and Globex's n8n (another client's)."""
    del database_url
    service = FakeN8n()
    service.add_workflow("wf1", "Sync leads", active=True)
    service.executions.append({"id": "ex1", "workflowId": "wf1", "status": "success"})
    monkeypatch.setattr(n8n, "transport", service.transport())
    get_vault().store("file://acme/n8n", API_KEY)
    get_vault().store("file://globex/n8n", API_KEY)
    get_vault().store(get_settings().claude_api_key_ref, CLAUDE_API_KEY)
    with session_scope() as session:
        make_project(session, ACME)
        make_project(session, GLOBEX, owner="globex")
        make_instance(session, "acme-n8n", type=InstanceType.N8N, secret_ref="file://acme/n8n",
                      base_url="https://n8n.acme.example")  # fmt: skip
        make_instance(session, "globex-n8n", type=InstanceType.N8N, owner="globex",
                      secret_ref="file://globex/n8n",
                      base_url="https://n8n.globex.example")  # fmt: skip
    bind(ACME, "acme-n8n")
    with session_scope() as session:
        agents.create_agent(session, ACME, AgentIn("Fixer", connections=("acme-n8n",)))
    return service


def watch(event: dict[str, Any], project: str = ACME) -> str:
    template = TEMPLATES["fix-it"]
    data = RoutineIn(template["name"], "fixer", template["prompt"], "event", event={**event})
    with session_scope() as session:
        made = routines.create_routine(session, project, data, tz="UTC")
        routines.set_paused(session, str(made.id), False, now=at("2026-11-07T09:00:00"))
        return str(made.id)


def tick(now: datetime) -> list[routines.Started]:
    """As the scheduler does it: the watches' looks first, then one transaction."""
    with session_scope(commit=False) as session:
        watches = routines.due_watches(session, get_settings(), now)
    fetched = routine_events.fetch_all(watches, now)
    with session_scope() as session:
        started = routines.tick(session, get_settings(), now, fetched)
        session.flush()
        for s in started:
            _ = s.run.event, s.run.skipped
        session.expunge_all()
        return started


def state_of(ident: str) -> dict[str, Any]:
    with session_scope(commit=False) as session:
        return dict(routines.get_routine(session, ident).state or {})


def test_a_failed_n8n_run_starts_one_fix_it_run(fake: FakeN8n) -> None:
    ident = watch({"source": "n8n", "connection": "acme-n8n"})
    assert tick(at("2026-11-07T09:00:30")) == []  # the first look only notes what's there
    fake.executions.append({"id": "ex2", "workflowId": "wf1", "status": "error"})
    assert tick(at("2026-11-07T09:02:00")) == []  # looked at every 5 minutes
    [fired] = tick(at("2026-11-07T09:06:00"))
    assert fired.run.event is not None
    assert fired.run.event["what"] == 'The n8n workflow "Sync leads" failed.'
    assert fired.run.event["execution_id"] == "ex2"
    assert tick(at("2026-11-07T09:12:00")) == []  # once per failure
    with session_scope(commit=False) as session:
        task = session.scalars(select(Task)).one()
        assert 'Something failed: The n8n workflow "Sync leads" failed.' in task.prompt
        assert task.project == ACME
    assert state_of(ident)["last_event"]["what"] == 'The n8n workflow "Sync leads" failed.'
    with session_scope(commit=False) as session:  # Home's line
        home = routines.watching(session)
    assert (home["watching"], home["cant_look"]) == (1, [])
    assert home["last"]["what"] == 'The n8n workflow "Sync leads" failed.'
    assert home["last"]["routine_id"] == ident
    # The gateway added the key; the watch never had it.
    assert all(r.headers["X-N8N-API-KEY"] == API_KEY for r in fake.requests)


def test_a_watch_can_t_read_another_client_s_connection(fake: FakeN8n) -> None:
    with session_scope() as session:  # a link forced past the app's own check
        project = session.scalars(select(Project).where(Project.name == ACME)).one()
        globex = session.scalars(select(Instance).where(Instance.name == "globex-n8n")).one()
        session.add(
            ProjectInstance(
                project_id=project.id, instance_id=globex.id,
                instance_type=globex.type, instance_env=globex.env,
            )
        )  # fmt: skip
    ident = watch({"source": "n8n", "connection": "globex-n8n"})
    fake.requests.clear()
    assert tick(at("2026-11-07T09:00:30")) == []
    assert "'globex-n8n' is not available to project 'acme-web'" in str(state_of(ident)["error"])
    assert fake.requests == []  # refused before anything was sent
    with session_scope(commit=False) as session:
        refused = session.scalars(
            select(AuditEvent).where(AuditEvent.session_id == f"watch:{ident}")
        ).one()
        assert (refused.outcome, refused.instance) == (AuditOutcome.REFUSED, "globex-n8n")
        assert session.scalars(select(RoutineRun)).all() == []
        [cant] = routines.watching(session, client="acme")["cant_look"]
    assert cant["routine_id"] == ident
    assert "not available to project" in cant["error"]


def test_a_watch_says_what_to_watch(fake: FakeN8n) -> None:
    del fake
    for event, says in [
        ({"source": "n8n", "connection": "nope"}, "Choose an n8n connection"),
        ({"source": "vercel", "connection": "acme-n8n", "project": "x"}, "Choose a Vercel"),
        ({"source": "github", "connection": "x", "repo": "acme/site"}, "Choose a GitHub"),
        ({"source": "website", "url": "ftp://acme.example"}, "starting with https://"),
        ({"source": "website", "url": "acme.example"}, "starting with https://"),
    ]:
        with pytest.raises(RuleViolationError, match=says):
            watch(event)
    watch({"source": "website", "url": "https://acme.example"})  # needs no connection


# --- What each look picks out (a fake read stands in for the gateway) ---


def reads(answers: dict[str, JSON]) -> routine_events.Read:
    async def read(tool: str, **args: Any) -> JSON:
        del args
        return answers[tool]

    return read


def look(source: str, event: dict[str, Any], answers: dict[str, JSON]) -> list[routine_events.Item]:
    import asyncio  # noqa: PLC0415

    return asyncio.run(routine_events.POLLERS[source](reads(answers), {"source": source, **event}))


def test_only_failed_vercel_deploys_count() -> None:
    found = look(
        "vercel", {"project": "acme-site"},
        {"list_deployments": {"deployments": [
            {"id": "d1", "url": "acme-site-d1.vercel.app", "state": "READY",
             "environment": "production"},
            {"id": "d2", "url": "acme-site-d2.vercel.app", "state": "ERROR",
             "environment": "production", "commit": "Fix the footer"},
        ]}},
    )  # fmt: skip
    assert [(i, f["what"], f["link"]) for i, f in found] == [
        ("d2", "A production deploy of acme-site on Vercel failed.", "https://acme-site-d2.vercel.app")
    ]  # fmt: skip


def test_failed_github_checks_and_new_issues() -> None:
    checks = look(
        "github", {"repo": "acme/site", "watch": "checks"},
        {"list_check_runs": {"branch": "main", "check_runs": [
            {"id": 1, "name": "build", "conclusion": "success", "sha": "abc"},
            {"id": 2, "name": "tests", "conclusion": "failure", "sha": "abc", "url": "https://x/2"},
            {"id": 3, "name": "lint", "conclusion": None, "status": "in_progress", "sha": "abc"},
        ]}},
    )  # fmt: skip
    assert [(i, f["what"]) for i, f in checks] == [
        ("check:2", 'The check "tests" failed on acme/site (main, abc).')
    ]
    issues = look(
        "github", {"repo": "acme/site", "watch": "issues"},
        {"list_issues": {"issues": [{"number": 7, "title": "Footer is broken", "url": "https://x/7"}]}},
    )  # fmt: skip
    assert [(i, f["what"]) for i, f in issues] == [
        ("issue:7", "New issue in acme/site: #7 Footer is broken")
    ]


def test_a_website_that_goes_down_is_one_event_until_it_s_back(
    fake: FakeN8n, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fake
    answers = [200, 503, 503, 200, 500]
    asked: list[str] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        asked.append(str(request.url))
        return httpx2.Response(answers.pop(0))

    monkeypatch.setattr(routine_events, "WEBSITE_TRANSPORT", httpx2.MockTransport(answer))
    ident = watch({"source": "website", "url": "https://acme.example"})
    assert tick(at("2026-11-07T09:00:30")) == []  # up: noted
    [down] = tick(at("2026-11-07T09:06:00"))
    assert down.run.event is not None
    assert down.run.event["what"] == "https://acme.example is down: it answered 503."
    assert tick(at("2026-11-07T09:12:00")) == []  # still down: the same outage
    assert tick(at("2026-11-07T09:18:00")) == []  # back up
    assert state_of(ident)["down_since"] is None
    [again] = tick(at("2026-11-07T09:24:00"))  # down again: a new event
    assert again.run.event is not None
    assert "answered 500" in again.run.event["what"]
    assert asked == ["https://acme.example"] * 5
