"""A client's month: what was done for them and what it cost, the report (Markdown, PDF, CSV),
the monthly cap, and the report emailed to them, held for approval like any send.

Two made-up clients: General Informatics LLC, with two projects, and Acme. One client's month
never shows the other's work.
"""

import asyncio
import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    AuditEvent,
    AuditOutcome,
    AuthMode,
    Decision,
    DecisionKind,
    DecisionStatus,
    InstanceType,
    Routine,
    TaskStatus,
)
from ringkeeper.services import (
    agents,
    calls,
    client_month,
    clients,
    inbox,
    pdf_text,
    routines,
    tasks,
)
from ringkeeper.vault import Vault, get_vault
from tests import test_core_connections, test_web_api
from tests.helpers import bind, make_instance, make_project

owner, admin, setup_code = test_web_api.owner, test_web_api.admin, test_web_api.setup_code
server = test_core_connections.server

GI, ACME = "General Informatics LLC", "Acme"
SITE, SHOP, PORTAL = "gi-site", "gi-shop", "acme-portal"


def this_month() -> str:
    now = client_month.local_now()
    return f"{now.year}-{now.month:02d}"


def ran(
    project: str,
    what: str,
    *,
    status: TaskStatus = TaskStatus.SUCCEEDED,
    cost: float | None = 0.5,
    summary: str = "",
    days_ago: float = 0,
) -> None:
    """A task as the worker leaves it."""
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project=project, prompt=what)
        task.status, task.summary, task.outcome = status, summary or None, what
        task.created_at = datetime.now(UTC) - timedelta(days=days_ago)
        task.sessions[0].cost_usd = cost
        task.sessions[0].status = status


@pytest.fixture
def two_clients(database_url: str) -> tuple[str, str]:
    del database_url
    with session_scope() as session:
        gi = clients.ensure(session, GI)
        acme = clients.ensure(session, ACME)
        make_project(session, SITE, owner=gi, title="Website")
        make_project(session, SHOP, owner=gi, title="Online shop")
        make_project(session, PORTAL, owner=acme, title="Customer portal")
    return gi, acme


def record(project: str, outcome: AuditOutcome, n: int = 1) -> None:
    with session_scope() as session:
        for _ in range(n):
            session.add(AuditEvent(project=project, outcome=outcome, tool="execute"))


def decided(project: str, status: DecisionStatus) -> None:
    now = datetime.now(UTC)
    with session_scope() as session:
        session.add(
            Decision(
                kind=DecisionKind.APPROVE,
                status=status,
                project=project,
                session_id="s",
                token_id="t",
                instance="db",
                service=InstanceType.SUPABASE,
                tool="execute",
                args={},
                args_hash="h",
                created_at=now,
                expires_at=now + timedelta(hours=1),
                decided_at=now,
                decided_by="owner",
            )
        )


def test_a_client_s_month_counts_only_their_projects_and_that_month(
    two_clients: tuple[str, str],
) -> None:
    gi, acme = two_clients
    ran(SITE, "Fix the contact form", summary="The form sends again.", cost=1.25)
    ran(SHOP, "Import the spring catalogue", status=TaskStatus.FAILED, cost=0.75)
    ran(SHOP, "Check the checkout", status=TaskStatus.RUNNING, cost=None)
    ran(SITE, "Last month's work", days_ago=45, cost=9.0)
    ran(PORTAL, "Acme's own task", cost=4.0)
    record(SITE, AuditOutcome.EXECUTED, 3)
    record(SHOP, AuditOutcome.HELD, 2)
    record(SHOP, AuditOutcome.REFUSED)
    record(PORTAL, AuditOutcome.EXECUTED, 7)
    decided(SHOP, DecisionStatus.EXECUTED)
    decided(SHOP, DecisionStatus.DENIED)
    decided(PORTAL, DecisionStatus.DENIED)

    now = client_month.local_now()
    with session_scope() as session:
        found = client_month.month_of(session, gi, now.year, now.month, AuthMode.API_KEY)
        assert client_month.spend(session, acme, now.year, now.month) == pytest.approx(4.0)
    assert [t.title for t in found.tasks] == [
        "Fix the contact form", "Import the spring catalogue", "Check the checkout",
    ]  # fmt: skip
    assert (found.done, found.failed, found.open) == (1, 1, 1)
    assert found.cost_usd == pytest.approx(2.0)
    assert (found.changes, found.held, found.refused) == (3, 2, 1)
    assert (found.approved, found.denied) == (1, 1)
    assert not found.estimated
    lines = client_month.summary_lines(found, cost=True)
    assert lines[0] == "3 tasks: 1 done, 1 didn't finish, 1 still open"
    assert "2 changes waited for your approval: 1 approved, 1 declined" in lines
    assert lines[-1] == "AI cost: $2.00"


def test_the_report_downloads_as_markdown_pdf_and_csv(
    two_clients: tuple[str, str], owner: TestClient
) -> None:
    gi, _ = two_clients
    ran(SITE, "Fix the contact form", summary="The form sends again.", cost=1.25)
    ran(SITE, '=HYPERLINK("http://evil.example")', cost=0.1)  # as a client might type it
    ran(PORTAL, "Acme's own task")
    month = this_month()
    label = client_month.label(*map(int, month.split("-")))

    md = owner.get(f"/clients/{gi}/month.md", params={"month": month})
    assert md.status_code == 200, md.text
    assert f'filename="{gi}-{month}.md"' in md.headers["content-disposition"]
    text = md.text
    assert text.startswith(f"# {GI}: {label}")
    assert "| Website | Fix the contact form | done: The form sends again. |" in text
    assert "Acme" not in text
    assert "$" not in text  # the cost only when asked for
    with_cost = owner.get(f"/clients/{gi}/month.md", params={"month": month, "cost": "true"})
    assert "AI cost: $1.35" in with_cost.text

    pdf = owner.get(f"/clients/{gi}/month.pdf", params={"month": month})
    assert pdf.content.startswith(b"%PDF-1.4")
    read = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert f"{GI}: {label}" in read
    assert "Fix the contact form" in read
    assert "• 2 tasks: 2 done" in read  # a real bullet, not "?"

    sheet = owner.get(f"/clients/{gi}/month.csv", params={"month": month})
    rows = list(csv.reader(io.StringIO(sheet.content.decode("utf-8-sig"))))
    assert rows[0] == client_month.CSV_HEAD
    assert [r[2] for r in rows[1:]] == [
        "Fix the contact form",
        '\'=HYPERLINK("http://evil.example")',
    ]
    assert rows[1][5] == "1.2500"

    future = owner.get(f"/clients/{gi}/month.md", params={"month": "2999-01"})
    assert future.status_code == 409
    assert future.json()["detail"] == "That month hasn't started yet."


def test_a_long_report_runs_onto_more_pages() -> None:
    body = "# Long\n\n## Tasks\n\n" + "\n".join(f"- line {i} " + "word " * 30 for i in range(120))
    reader = PdfReader(io.BytesIO(pdf_text.render(body, "Long")))
    assert len(reader.pages) > 3
    assert "line 119" in reader.pages[-1].extract_text()


def over_cap_question(task_id: str) -> Decision:
    with session_scope() as session:
        [asked] = session.scalars(
            select(Decision).where(Decision.task_id == uuid.UUID(task_id))
        ).all()
        session.expunge(asked)
        return asked


def answer(decision: Decision, reply: str) -> None:
    with session_scope() as session:
        inbox.answer(session, get_settings(), str(decision.id), inbox.Answer(text=reply),
                     by="owner", via=inbox.Via.API)  # fmt: skip


def new_task(project: str, prompt: str = "One more thing") -> str:
    with session_scope() as session:
        return str(tasks.create_task(session, get_settings(), project=project, prompt=prompt).id)


def status_of(task_id: str) -> tuple[TaskStatus, list[TaskStatus]]:
    with session_scope() as session:
        task = tasks.get_task(session, task_id)
        return task.status, [s.status for s in task.sessions]


def test_over_the_monthly_cap_a_new_task_asks_first(
    two_clients: tuple[str, str], owner: TestClient
) -> None:
    gi, acme = two_clients
    ran(SITE, "Last month's big job", days_ago=45, cost=50.0)  # not this month's
    ran(SHOP, "This month", cost=1.2)
    set_cap = owner.put(f"/clients/{gi}/cap", json={"cap_usd": 1.0})
    assert set_cap.status_code == 200, set_cap.text

    listed = {c["slug"]: c for c in owner.get("/clients").json()}
    assert listed[gi]["month_usd"] == pytest.approx(1.2)
    assert listed[gi]["cap_usd"] == 1.0
    assert listed[acme]["cap_usd"] is None

    # Over the cap, the task waits for the user instead of running (no worker picks it up).
    first = new_task(SITE)
    assert status_of(first) == (TaskStatus.NEEDS_INPUT, [TaskStatus.NEEDS_INPUT])
    asked = over_cap_question(first)
    assert asked.kind is DecisionKind.QUESTION
    assert (asked.payload or {})["question"].startswith(
        f"{GI} is over its monthly cap: $1.20 of $1.00 in "
    )
    with session_scope() as session:
        shown = session.get(Decision, asked.id)
        assert shown is not None
        assert inbox.is_approval(shown)  # it's in Approvals
        assert tasks.claim_next(session) is None
    assert status_of(new_task(PORTAL))[0] is TaskStatus.QUEUED  # Acme has no cap

    answer(asked, "run once")
    assert status_of(first) == (TaskStatus.QUEUED, [TaskStatus.QUEUED])

    second = new_task(SITE, "And another")
    with pytest.raises(RuleViolationError, match=r"above what .* has used this month \(\$1\.20\)"):
        answer(over_cap_question(second), "raise:1.10")
    answer(over_cap_question(second), "raise:5")
    assert status_of(second)[0] is TaskStatus.QUEUED
    assert {c["slug"]: c for c in owner.get("/clients").json()}[gi]["cap_usd"] == 5.0
    assert status_of(new_task(SITE, "Under the new cap"))[0] is TaskStatus.QUEUED

    owner.put(f"/clients/{gi}/cap", json={"cap_usd": 1.0})
    third = new_task(SITE, "Not this one")
    answer(over_cap_question(third), "don't run it")
    with session_scope() as session:
        stopped = tasks.get_task(session, third)
        assert (stopped.status, stopped.error) == (
            TaskStatus.STOPPED, f"not run: {GI} is over its monthly cap",
        )  # fmt: skip
        logged = session.scalars(
            select(AuditEvent).where(AuditEvent.tool.in_(("client.cap", "client.cap.once")))
        ).all()
    said = [(e.tool, (e.reason or "").split(" (by")[0]) for e in logged]
    assert said == [
        ("client.cap", f"set {GI}'s monthly cap to $1.00"),
        (
            "client.cap.once",
            f"ran a task for {GI} past its monthly cap, this once ($1.20 of $1.00)",
        ),
        ("client.cap", f"raised {GI}'s monthly cap to $5.00 (from $1.00) to run a task"),
        ("client.cap", f"set {GI}'s monthly cap to $1.00"),
    ]


def test_routines_still_skip_over_the_cap_and_home_says_why(
    two_clients: tuple[str, str], owner: TestClient
) -> None:
    gi, _ = two_clients
    ran(SHOP, "This month", cost=1.2)
    owner.put(f"/clients/{gi}/cap", json={"cap_usd": 1.0})
    with session_scope() as session:
        agents.create_agent(session, SITE, agents.AgentIn("checker"))
        data = routines.RoutineIn(
            "Weekly check", "checker", "Check the site", "schedule", {"every": "hours", "hours": 1}
        )
        routine = routines.create_routine(session, SITE, data)
        started = routines.start_run(session, get_settings(), routine, "manual")
        assert started.run.task_id is None
        assert f"{GI} is over its monthly cap" in (started.run.skipped or "")
        ident = routine.id
    assert owner.get("/watching").json()["capped"] == []  # it's paused: nothing waits
    with session_scope() as session:
        found = session.get(Routine, ident)
        assert found is not None
        found.paused = False
    capped = owner.get("/watching").json()["capped"]
    assert capped == [{"client": gi, "name": GI, "used": 1.2, "cap": 1.0, "routines": 1}]
    owner.put(f"/clients/{gi}/cap", json={"cap_usd": None})
    assert owner.get("/watching").json()["capped"] == []


SMTP = test_core_connections.SMTP_SECRET


def mail(vault: Vault, name: str, owner_slug: str, project: str) -> None:
    vault.store(f"file://{owner_slug}/{name}", SMTP)
    with session_scope() as session:
        make_instance(
            session, name, type=InstanceType.SMTP, owner=owner_slug,
            secret_ref=f"file://{owner_slug}/{name}",
        )  # fmt: skip
    bind(project, name)


def test_the_report_is_emailed_only_once_approved(
    two_clients: tuple[str, str],
    owner: TestClient,
    server: tuple[Any, test_core_connections.FakeSmtp],
) -> None:
    gi, acme = two_clients
    _, sender = server
    vault = get_vault()
    mail(vault, "gi-mail", gi, SHOP)
    mail(vault, "acme-mail", acme, PORTAL)
    ran(SITE, "Fix the contact form", summary="The form sends again.")
    month = this_month()

    shown = owner.get(f"/clients/{gi}/month", params={"month": month}).json()
    assert [m["connection"] for m in shown["mail"]] == ["gi-mail"]  # never Acme's

    other = owner.post(
        f"/clients/{gi}/month/email",
        json={"month": month, "to": "ops@generalinformatics.example", "connection": "acme-mail"},
    )
    assert other.status_code == 422
    assert "isn't a mail connection" in other.json()["detail"]

    held = owner.post(
        f"/clients/{gi}/month/email",
        json={"month": month, "to": "ops@generalinformatics.example", "connection": "gi-mail"},
    )
    assert held.status_code == 200, held.text
    assert not sender.sent  # a draft, waiting in Approvals
    with session_scope() as session:
        decision = session.get(Decision, uuid.UUID(held.json()["decision_id"]))
        assert decision is not None
        assert (decision.status, decision.tool, decision.project) == (
            DecisionStatus.PENDING, "send_email", SHOP,
        )  # fmt: skip
        assert decision.args is not None
        assert decision.args["to"] == ["ops@generalinformatics.example"]
        assert decision.args["body"].startswith(f"# {GI}: ")

    asyncio.run(calls.approve(held.json()["decision_id"], by="owner", vault=vault))
    [sent] = sender.sent
    assert GI in json.dumps(str(sent))


def test_over_the_cap_a_follow_up_asks_first(two_clients: tuple[str, str]) -> None:
    gi, _ = two_clients
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project=SITE, prompt="Start")
        task.status = task.sessions[0].status = TaskStatus.SUCCEEDED
        task.sessions[0].sdk_session_id = "sdk-1"
        task.sessions[0].cost_usd = 3.0
        ident = str(task.id)
        clients.get(session, gi).monthly_cap_usd = 2.0
    with session_scope() as session:
        tasks.resume_task(session, get_settings(), ident, "And one more")
    assert status_of(ident) == (TaskStatus.NEEDS_INPUT, [TaskStatus.SUCCEEDED])
    asked = over_cap_question(ident)
    assert "Run this follow-up anyway" in (asked.payload or {})["question"]

    answer(asked, "Run anyway this once")  # as typed in a chat
    with session_scope() as session:
        task = tasks.get_task(session, ident)
        assert [s.prompt for s in task.sessions] == ["Start", "And one more"]
        assert task.sessions[1].sdk_session_id == "sdk-1"  # it goes on from there


def test_ringkeeper_ask_over_the_cap_gives_the_approval_link(
    two_clients: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from ringkeeper.calls import Caller  # noqa: PLC0415
    from ringkeeper.services import pass_tasks, passes  # noqa: PLC0415

    gi, _ = two_clients
    monkeypatch.setenv("RINGKEEPER_MODE", "desktop")
    ran(SHOP, "This month", cost=1.2)
    with session_scope() as session:
        clients.get(session, gi).monthly_cap_usd = 1.0
        passes.create(session, get_vault(), "gi-desktop", SITE)
    app = Caller(SITE, "pass:gi-desktop", "pass:gi-desktop")
    with session_scope() as session:
        task = pass_tasks.start(session, get_settings(), app, "Check the site")
        found = pass_tasks.status(session, get_settings(), app, str(task.id))
    assert found["status"] == "held"
    assert "%2Fapprovals&sig=" in found["open"]
