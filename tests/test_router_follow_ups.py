"""Follow-ups in the same conversation: a short message naming a project answers the open
"which project?", and the original request runs there with no second question; "yes" is a
production go-ahead; a message naming a project places a request that couldn't be placed."""

from typing import Any

import pytest
from sqlalchemy import select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Decision, DecisionStatus, Env, Message, Task
from ringkeeper.router.follow_up import named_project
from ringkeeper.router.service import route_next
from ringkeeper.services import inbox, threads
from tests.fake_router import FakeModel, output, routed
from tests.helpers import bind, make_instance, make_project


@pytest.fixture(autouse=True)
def projects(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "gi-site", owner="General Informatics LLC", title="Website")
        make_project(session, "gi-blog", owner="General Informatics LLC", title="Blog")
        make_project(session, "my-project", owner="Alexey", title="My Project")


def say(body: str, thread: str | None = None, source: str = "web") -> str:
    with session_scope() as session:
        author = f"{source}:U1" if source != "web" else "owner"
        message = threads.post_message(session, body, author=author, source=source, thread=thread)
        return str(message.thread_id)


def route(*answers: Any) -> None:
    assert route_next(get_settings(), FakeModel(list(answers))) is not None


def started() -> list[tuple[str, str]]:
    with session_scope() as session:
        rows = session.scalars(select(Task).order_by(Task.created_at)).all()
        return [(t.project, t.prompt) for t in rows]


def open_questions() -> int:
    with session_scope() as session:
        found = session.scalars(select(Decision).where(Decision.status == DecisionStatus.PENDING))
        return len(found.all())


UNSURE = output(
    routed("gi-site", 0.4, prompt="Fix the contact form.", alternatives=[
        {"project": "gi-blog", "confidence": 0.3},
    ])
)  # fmt: skip


@pytest.mark.parametrize("source", ["web", "slack", "discord", "telegram"])
@pytest.mark.parametrize("reply", ["Blog", "the blog one", "General Informatics, Blog"])
def test_naming_the_project_answers_which_project(source: str, reply: str) -> None:
    thread = say("Fix the contact form", source=source)
    route(UNSURE)
    assert open_questions() == 1
    say(reply, thread, source=source)
    route()  # no model call: the follow-up is the answer
    assert started() == [("gi-blog", "Fix the contact form.")]
    assert open_questions() == 0
    with session_scope() as session:
        last = session.scalars(select(Message).order_by(Message.created_at.desc())).first()
        assert last is not None
        assert "What would you like" not in last.body


def test_none_of_these_answers_it_too() -> None:
    thread = say("Fix the contact form")
    route(UNSURE)
    say("neither", thread)
    route()
    assert started() == []
    assert open_questions() == 0


def test_yes_is_the_go_ahead_on_production() -> None:
    with session_scope() as session:
        make_instance(
            session, "gi-db", env=Env.PROD, owner="General Informatics LLC",
            secret_ref="aws-sm://gi/db",
        )  # fmt: skip
    bind("gi-site", "gi-db")
    thread = say("How many orders came in today?")
    route(output(routed("gi-site", 0.95, kind="data")))
    assert open_questions() == 1
    say("yes, go ahead", thread)
    route()
    assert [p for p, _ in started()] == ["gi-site"]


def test_naming_a_project_places_a_request_that_couldn_t_be_placed() -> None:
    thread = say("Look into the broken signup")
    route(output(reply="I can't tell which project this is for."))  # nothing started
    assert started() == []
    say("Alexey, My Project", thread)
    model = FakeModel([output(routed("gi-site", 0.9, prompt="Look into the broken signup."))])
    assert route_next(get_settings(), model) is not None
    assert started() == [("my-project", "Look into the broken signup.")]
    prompt = model.calls[0][1]
    assert "MESSAGE TO ROUTE:\nLook into the broken signup" in prompt


def test_a_longer_message_is_a_request_of_its_own() -> None:
    thread = say("Fix the contact form")
    route(UNSURE)
    say("Also, separately, check that the Blog's sitemap lists every post from this year", thread)
    route(output(routed("gi-blog", 0.95)))
    assert open_questions() == 1  # the question still waits
    assert [p for p, _ in started()] == ["gi-blog"]


def test_names_are_matched_as_whole_words() -> None:
    with session_scope() as session:
        assert named_project(session, "My Project please") == "my-project"
        assert named_project(session, "Alexey") == "my-project"  # the client's only project
        assert named_project(session, "General Informatics") is None  # two projects, no default
        assert named_project(session, "website or blog") is None  # two: not an answer
        assert named_project(session, "blogging tips") is None
        assert inbox.NO_PROJECT
