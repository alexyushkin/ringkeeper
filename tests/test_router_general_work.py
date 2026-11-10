"""Requests that need no client connection, and the client and project chosen at the top of the
app: a project chosen there decides, like an @-mention; general work (a web lookup, writing)
runs there, or in the operator's own client's default project; and a project whose tasks can't
use the web says so plainly."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Autonomy, Decision, Message, Project, Task
from ringkeeper.router.service import route_next
from ringkeeper.services import clients, threads
from tests import test_web_api
from tests.fake_router import FakeModel, output, routed
from tests.helpers import make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner


@pytest.fixture(autouse=True)
def projects(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "gi-site", owner="General Informatics LLC", title="Website")
        make_project(session, "gi-blog", owner="General Informatics LLC", title="Blog")
        make_project(
            session, "my-project", owner="Alexey", title="My Project", autonomy=Autonomy.RELAXED
        )


def ask(body: str, **fields: Any) -> str:
    with session_scope() as session:
        message = threads.post_message(session, body, author="owner", source="web", **fields)
        return str(message.thread_id)


def route(*answers: dict[str, Any]) -> None:
    assert route_next(get_settings(), FakeModel(list(answers))) is not None


def started() -> list[str]:
    with session_scope() as session:
        return [t.project for t in session.scalars(select(Task).order_by(Task.created_at))]


def asked() -> int:
    with session_scope() as session:
        return len(session.scalars(select(Decision)).all())


def last_reply(thread: str) -> str:
    with session_scope() as session:
        return threads.thread_messages(session, thread)[-1].body  # type: ignore[arg-type]


def test_a_project_chosen_at_the_top_decides() -> None:
    ask("Fix the contact form", target_client="general-informatics-llc", target_project="gi-blog")
    unsure = routed("gi-site", 0.4, alternatives=[{"project": "gi-blog", "confidence": 0.3}])
    route(output(unsure))
    assert started() == ["gi-blog"]
    assert asked() == 0


def test_general_work_runs_in_your_own_client_s_default_project() -> None:
    with session_scope() as session:
        clients.set_own(session, "alexey", True)
    ask("Look up what year the Eiffel Tower opened")
    route(output(routed("gi-site", 0.3, general=True, needs_web=True)))
    assert started() == ["my-project"]
    assert asked() == 0


def test_general_work_runs_in_the_chosen_project() -> None:
    with session_scope() as session:
        clients.set_own(session, "alexey", True)
        session.scalars(
            select(Project).where(Project.name == "gi-blog")
        ).one().autonomy = Autonomy.RELAXED
    ask("Summarize this pasted text: ...", target_project="gi-blog")
    route(output(routed("my-project", 0.9, general=True)))
    assert started() == ["gi-blog"]


def test_a_project_without_the_web_says_so_plainly() -> None:
    thread = ask("Look up today's exchange rate", target_project="gi-site")
    route(output(routed("gi-site", 0.9, general=True, needs_web=True)))
    assert started() == []
    said = last_reply(thread)
    assert said.startswith("Tasks in Website can't use the web: its autonomy is standard")
    assert "set Website to relaxed on its Settings tab" in said
    assert "none of" not in said.lower()


def test_only_one_client_is_your_own() -> None:
    with session_scope() as session:
        clients.set_own(session, "alexey", True)
        clients.set_own(session, "general-informatics-llc", True)
        assert clients.own_client(session) == "general-informatics-llc"


def test_the_app_sends_the_chosen_project(owner: TestClient) -> None:
    sent = owner.post(
        "/messages",
        json={
            "body": "Fix the footer",
            "source": "web",
            "client": "alexey",
            "project": "my-project",
        },
    )
    assert sent.status_code == 202, sent.text
    with session_scope() as session:
        message = session.scalars(select(Message)).one()
        assert (message.target_client, message.target_project) == ("alexey", "my-project")
    marked = owner.put("/clients/alexey/own", json={"own": True})
    assert marked.json()["own"] is True


# --- The switcher vs. what the request says ---


def decisions() -> list[Decision]:
    with session_scope() as session:
        found = session.scalars(select(Decision)).all()
        session.expunge_all()
        return list(found)


def test_an_at_mention_wins_over_the_project_chosen_at_the_top() -> None:
    ask("@Website fix the footer", target_client="alexey", target_project="my-project")
    route(output(routed("my-project", 0.9)))
    assert started() == ["gi-site"]


def test_a_request_for_another_client_is_asked_about_once() -> None:
    ask("Fix General Informatics' contact form", target_project="my-project")
    route(output(routed("gi-site", 0.95)))  # the model is sure: another client's project
    assert started() == []
    [question] = decisions()
    payload = question.payload or {}
    assert payload["reason"] == (
        "This looks like it's for General Informatics LLC, but My Project is selected."
    )
    assert [c["project"] for c in payload["candidates"]] == ["gi-site", "my-project"]


def test_an_unsure_model_doesn_t_override_the_choice() -> None:
    ask("Fix the contact form", target_project="my-project")
    route(output(routed("gi-site", 0.4)))
    assert started() == ["my-project"]
