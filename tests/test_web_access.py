"""Web access is a project setting of its own, apart from autonomy: on for your own client's
projects unless you turn it off, off for a client's unless you allow it. A task that needs
the web where it's off asks once, with "Allow web for this project and retry"."""

from typing import Any

import pytest
from sqlalchemy import select

from ringkeeper.channels import cards
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import AuditEvent, Autonomy, Decision, DecisionStatus, Project, Task
from ringkeeper.router.service import route_next
from ringkeeper.services import clients, inbox, threads, web_access
from ringkeeper.telegram import bridge
from tests.fake_router import FakeModel, output, routed
from tests.helpers import make_project


@pytest.fixture(autouse=True)
def projects(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "gi-site", owner="General Informatics LLC", title="Website")
        make_project(session, "gi-blog", owner="General Informatics LLC", title="Blog")
        make_project(session, "my-project", owner="Alexey", title="My Project")
        clients.set_own(session, "alexey", True)


def ask(body: str, **fields: Any) -> str:
    with session_scope() as session:
        message = threads.post_message(session, body, author="owner", source="web", **fields)
        return str(message.thread_id)


def route(*answers: dict[str, Any]) -> None:
    assert route_next(get_settings(), FakeModel(list(answers))) is not None


def started() -> list[str]:
    with session_scope() as session:
        return [t.project for t in session.scalars(select(Task).order_by(Task.created_at))]


def last_reply(thread: str) -> str:
    with session_scope() as session:
        return threads.thread_messages(session, thread)[-1].body  # type: ignore[arg-type]


def open_decision() -> str:
    with session_scope() as session:
        return str(session.scalars(select(Decision)).one().id)


def press(ident: str, choice: str) -> inbox.Answered:
    with session_scope() as session:
        done = inbox.answer(
            session, get_settings(), ident, inbox.Answer(choice=choice), by="alice",
            via=inbox.Via.TELEGRAM,
        )  # fmt: skip
        session.expunge_all()
        return done


def web(name: str) -> bool:
    with session_scope() as session:
        project = session.scalars(select(Project).where(Project.name == name)).one()
        return web_access.allowed(session, project)


def test_the_default_is_on_for_your_own_projects_and_off_for_a_client_s() -> None:
    assert web("my-project")
    assert not web("gi-site")
    with session_scope() as session:
        mine = session.scalars(select(Project).where(Project.name == "my-project")).one()
        mine.web = False
        theirs = session.scalars(select(Project).where(Project.name == "gi-site")).one()
        theirs.web = True
    assert not web("my-project")
    assert web("gi-site")


def test_autonomy_no_longer_decides_the_web() -> None:
    with session_scope() as session:
        mine = session.scalars(select(Project).where(Project.name == "my-project")).one()
        mine.autonomy = Autonomy.STRICT
        theirs = session.scalars(select(Project).where(Project.name == "gi-site")).one()
        theirs.autonomy = Autonomy.RELAXED
    assert web("my-project")
    assert not web("gi-site")


def test_your_own_project_runs_a_web_lookup_at_once() -> None:
    ask("Look up today's exchange rate", target_project="my-project")
    route(output(routed("my-project", 0.9, general=True, needs_web=True)))
    assert started() == ["my-project"]


def test_a_client_s_project_asks_to_allow_the_web_then_runs_it() -> None:
    thread = ask("Look up today's exchange rate", target_project="gi-site")
    route(output(routed("gi-site", 0.9, general=True, needs_web=True)))
    assert started() == []
    said = last_reply(thread)
    assert said == "Tasks in Website can't use the web: it's off for this project."
    assert "relaxed" not in said
    assert "autonomy" not in said
    ident = open_decision()
    with session_scope() as session:
        decision = session.get(Decision, ident)
        assert decision is not None
        card = cards.decision_card(decision, None)
        assert card.text == f"{web_access.ASKS}\n{said}"
        assert [b.label for b in card.buttons] == [web_access.ALLOW, web_access.NOT_NOW]
        labels = [b["text"] for row in bridge.keyboard(decision)["inline_keyboard"] for b in row]
        assert labels == [web_access.ALLOW, web_access.NOT_NOW]
        assert "relaxed" not in bridge.render(decision, get_settings())
        assert inbox.pressed(decision, "gi-site").startswith("OK: the web is allowed for Website")
    done = press(ident, "gi-site")
    assert done.task is not None
    assert started() == ["gi-site"]
    assert web("gi-site")
    with session_scope() as session:
        audit = session.scalars(select(AuditEvent).where(AuditEvent.tool == "project.web")).one()
        assert audit.project == "gi-site"


def test_not_now_leaves_it_off_and_starts_nothing() -> None:
    ask("Look up today's exchange rate", target_project="gi-site")
    route(output(routed("gi-site", 0.9, general=True, needs_web=True)))
    press(open_decision(), inbox.NO_PROJECT)
    assert started() == []
    assert not web("gi-site")


def test_picking_a_project_without_the_web_asks_that_next() -> None:
    ask("Look up the Website and Blog's competitors online")
    unsure = routed(
        "gi-site", 0.4, needs_web=True, alternatives=[{"project": "gi-blog", "confidence": 0.3}]
    )
    route(output(unsure))
    which = open_decision()
    assert press(which, "gi-blog").task is None
    with session_scope() as session:
        rows = session.scalars(
            select(Decision).where(Decision.status == DecisionStatus.PENDING)
        ).all()
        assert [(d.payload or {}).get("web") for d in rows] == ["Blog"]
        allow = str(rows[0].id)
    press(allow, "gi-blog")
    assert started() == ["gi-blog"]
