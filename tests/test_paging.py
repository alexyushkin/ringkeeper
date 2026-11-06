"""Long lists: every row once, however they're paged; search reaching past what's loaded; and the
counts the pages show."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.models import (
    Agent,
    AuditEvent,
    AuditOutcome,
    AuthMode,
    Decision,
    DecisionKind,
    DecisionStatus,
    InstanceType,
    Message,
    MessageRole,
    Project,
    Routine,
    RoutineRun,
    Task,
    TaskStatus,
    Thread,
)
from ringkeeper.services import decisions, inbox, paging, routines
from tests import test_web_api
from tests.helpers import make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def walk(http: TestClient, path: str, **query: Any) -> tuple[list[dict[str, Any]], list[int]]:
    """Every page of a list: the rows, and the total each page said."""
    rows: list[dict[str, Any]] = []
    totals: list[int] = []
    cursor: str | None = None
    while True:
        got = http.get(path, params={**query, **({"cursor": cursor} if cursor else {})})
        assert got.status_code == 200, got.text
        rows += got.json()
        totals.append(int(got.headers[paging.TOTAL_HEADER]))
        cursor = got.headers.get(paging.NEXT_HEADER)
        if cursor is None:
            return rows, totals


def add_threads(count: int, *, title: str = "Thread", archived: bool = False) -> list[uuid.UUID]:
    with session_scope() as session:
        made = [
            Thread(
                title=f"{title} {n}", source="web", updated_at=NOW - timedelta(minutes=n),
                archived_at=NOW if archived else None,
            )
            for n in range(count)
        ]  # fmt: skip
        session.add_all(made)
        session.flush()
        return [t.id for t in made]


def said(thread: uuid.UUID, body: str) -> Message:
    return Message(thread_id=thread, role=MessageRole.USER, author="owner", source="web", body=body)


def test_every_row_comes_once_and_newest_first(owner: TestClient) -> None:
    made = add_threads(7)
    rows, totals = walk(owner, "/threads", limit=3)
    assert [r["id"] for r in rows] == [str(i) for i in made]
    assert totals == [7, 7, 7]


def test_rows_with_the_same_time_are_neither_lost_nor_repeated(owner: TestClient) -> None:
    with session_scope() as session:
        session.add_all(Thread(title=f"T{n}", source="web", updated_at=NOW) for n in range(9))
    rows, _ = walk(owner, "/threads", limit=2)
    assert len({r["id"] for r in rows}) == len(rows) == 9


def test_times_the_database_wrote_page_like_any_other(owner: TestClient) -> None:
    """SQLite's own clock writes a time in another text form than Ringkeeper does."""
    with session_scope() as session:
        session.add_all(Thread(title=f"T{n}", source="web") for n in range(5))  # its clock
        session.add_all(
            Thread(title=f"U{n}", source="web", updated_at=datetime.now(UTC)) for n in range(5)
        )
    rows, _ = walk(owner, "/threads", limit=2)
    assert len({r["id"] for r in rows}) == len(rows) == 10


def test_a_row_deleted_between_pages_doesnt_end_the_list(owner: TestClient) -> None:
    add_threads(6)
    first = owner.get("/threads", params={"limit": 3})
    with session_scope() as session:
        session.execute(
            text("DELETE FROM threads WHERE title = :t"), {"t": first.json()[-1]["title"]}
        )
    rest = owner.get("/threads", params={"limit": 3, "cursor": first.headers[paging.NEXT_HEADER]})
    assert [r["title"] for r in rest.json()] == ["Thread 3", "Thread 4", "Thread 5"]


def test_a_cursor_that_makes_no_sense_is_refused_plainly(owner: TestClient) -> None:
    refused = owner.get("/threads", params={"cursor": "not-a-cursor"})
    assert refused.status_code in (409, 422)
    assert "isn't there" in refused.text


def test_search_reaches_past_the_first_page_and_into_messages(owner: TestClient) -> None:
    made = add_threads(60)
    with session_scope() as session:
        session.add(said(made[-1], "the walrus invoice"))
    found = owner.get("/threads", params={"q": "walrus", "limit": 50})
    assert [r["id"] for r in found.json()] == [str(made[-1])]
    assert found.headers[paging.TOTAL_HEADER] == "1"
    named = owner.get("/threads", params={"q": "Thread 5", "limit": 3})
    assert named.headers[paging.TOTAL_HEADER] == "11"  # 5 and 50 to 59
    assert named.headers.get(paging.NEXT_HEADER)


def test_a_search_finds_archived_ones_and_says_how_many(owner: TestClient) -> None:
    add_threads(2, title="Active")
    add_threads(3, title="Old", archived=True)
    assert owner.get("/threads").headers[paging.TOTAL_HEADER] == "2"
    assert owner.get("/threads", params={"archived": True}).headers[paging.TOTAL_HEADER] == "3"
    assert owner.get("/threads", params={"q": "Old"}).headers[paging.TOTAL_HEADER] == "3"


def add_tasks(project: str, count: int, *, thread: uuid.UUID | None = None) -> None:
    with session_scope() as session:
        session.add_all(
            Task(project=project, prompt=f"Job {n}", status=TaskStatus.SUCCEEDED, max_turns=5,
                 auth_mode=AuthMode.API_KEY, max_runtime_s=60, changed_files=[], thread_id=thread,
                 created_at=NOW - timedelta(minutes=n))
            for n in range(count)
        )  # fmt: skip


def test_tasks_page_and_search_their_conversation(owner: TestClient) -> None:
    (thread,) = add_threads(1, title="Quarterly close")
    with session_scope() as session:
        make_project(session, "acme-web")
        session.add(said(thread, "reconcile the walrus"))
    add_tasks("acme-web", 5)
    add_tasks("acme-web", 1, thread=thread)
    rows, totals = walk(owner, "/tasks", limit=2)
    assert len({r["id"] for r in rows}) == 6
    assert set(totals) == {6}
    for words in ("walrus", "Quarterly"):
        found = owner.get("/tasks", params={"q": words})
        assert found.headers[paging.TOTAL_HEADER] == "1", words


def test_activity_pages_and_searches_what_was_sent(owner: TestClient) -> None:
    with session_scope() as session:
        session.add_all(
            AuditEvent(outcome=AuditOutcome.EXECUTED, tool="run", reason=f"Step {n}", at=NOW,
                       args={"note": "walrus"} if n == 0 else None)
            for n in range(7)
        )  # fmt: skip
    rows, _ = walk(owner, "/audit", limit=3, q="Step")
    assert len({r["id"] for r in rows}) == 7
    assert owner.get("/audit", params={"q": "walrus"}).headers[paging.TOTAL_HEADER] == "1"
    exported = owner.get("/audit.csv", params={"q": "walrus"}).text
    assert exported.count("\n") == 2  # the header and the one row


def hold_many(project: str, count: int, *, task: uuid.UUID | None = None) -> list[uuid.UUID]:
    made: list[uuid.UUID] = []
    args = {"sql": "update t set a = 1"}
    caller = Caller(project, "session-1", "token-1")
    call = ToolCall(caller, InstanceType.SUPABASE, "run", "db", args)
    with session_scope() as session:
        for _ in range(count):
            decision = decisions.hold(session, call, "db")
            decision.task_id = task
            made.append(decision.id)
    return made


def test_approvals_count_past_any_page(owner: TestClient) -> None:
    with session_scope() as session:
        make_project(session, "acme-web")
    hold_many("acme-web", 6)
    rows, totals = walk(owner, "/decisions", status="pending", category="approval", limit=4)
    assert len({r["id"] for r in rows}) == 6
    assert totals == [6, 6]
    # The badge asks for one row, and reads the count.
    one = owner.get("/decisions", params={"category": "approval", "limit": 1})
    assert (len(one.json()), one.headers[paging.TOTAL_HEADER]) == (1, "6")


def test_the_approvals_query_says_what_is_approval_says(owner: TestClient) -> None:
    """The query and the rule side by side, for every kind of decision."""
    with session_scope() as session:
        make_project(session, "acme-web")
    hold_many("acme-web", 1)
    with session_scope() as session:
        held = session.scalars(select(Decision)).one()
        for kind, payload in (
            (DecisionKind.PLAN, {}),
            (DecisionKind.QUESTION, {"question": "Which?"}),
            (DecisionKind.QUESTION, {"question": "More?", "budget": 2.5}),
            (DecisionKind.ROUTE, {"message": "x"}),
        ):
            session.add(Decision(
                project=held.project, session_id=held.session_id, token_id=held.token_id,
                status=DecisionStatus.PENDING, kind=kind, payload=payload,
                expires_at=held.expires_at,
            ))  # fmt: skip
    with session_scope() as session:
        every = session.scalars(select(Decision)).all()
        wanted = {d.id for d in every if inbox.is_approval(d)}
        found = inbox.page_of_decisions(session, approvals=True, limit=50).items
        others = inbox.page_of_decisions(session, approvals=False, limit=50).items
        assert {d.id for d in found} == wanted
        assert {d.id for d in others} == {d.id for d in every} - wanted


def test_a_page_of_a_task_or_thread_sees_only_its_own_decisions(owner: TestClient) -> None:
    """They used to read the newest 200 waiting anywhere, and look for theirs in those."""
    (thread,) = add_threads(1)
    with session_scope() as session:
        make_project(session, "acme-web")
    add_tasks("acme-web", 1, thread=thread)
    with session_scope() as session:
        task = session.scalars(select(Task)).one().id
    mine = hold_many("acme-web", 1, task=task)
    hold_many("acme-web", 5)
    by_task = owner.get("/decisions", params={"task": str(task)}).json()
    by_thread = owner.get("/decisions", params={"thread": str(thread)}).json()
    assert [d["id"] for d in by_task] == [str(mine[0])]
    assert [d["id"] for d in by_thread] == [str(mine[0])]


def test_routine_runs_page_and_a_routine_sees_its_own_decisions(owner: TestClient) -> None:
    with session_scope() as session:
        project = make_project(session, "acme-web")
        agent = Agent(project_id=project.id, name="bookkeeper", instructions="x")
        session.add(agent)
        session.flush()
        routine = Routine(
            project_id=project.id, agent_id=agent.id, name="Weekly", prompt="p", trigger="manual"
        )
        session.add(routine)
        session.flush()
        ident = routine.id
    add_tasks("acme-web", 1)
    with session_scope() as session:
        task = session.scalars(select(Task)).one().id
        session.add_all(
            RoutineRun(routine_id=ident, trigger="manual", task_id=task if n == 0 else None,
                       created_at=NOW - timedelta(minutes=n))
            for n in range(5)
        )  # fmt: skip
    rows, totals = walk(owner, f"/routines/{ident}/runs", limit=2)
    assert len({r["id"] for r in rows}) == 5
    assert set(totals) == {5}
    theirs = hold_many("acme-web", 1, task=task)
    hold_many("acme-web", 3)
    found = owner.get("/decisions", params={"routine": str(ident)}).json()
    assert [d["id"] for d in found] == [str(theirs[0])]
    with session_scope() as session:
        assert len(routines.history(session, str(ident), 3)) == 3
        assert session.get(Project, project.id) is not None
