"""Fewer "which project?" questions: never with one option, not again in the same
conversation, and not for work any of a client's projects would do (its default project).
When it asks, it says why in one line and offers to set that default."""

from typing import Any

import pytest
from sqlalchemy import select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Client, Decision, DecisionKind, DecisionStatus, InstanceType, Task
from ringkeeper.router.service import route_next
from ringkeeper.services import clients, inbox, threads
from ringkeeper.services.inbox import Answer, Via
from tests.fake_router import FakeModel, output, routed
from tests.helpers import bind, make_instance, make_project


@pytest.fixture(autouse=True)
def projects(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "gi-site", owner="General Informatics LLC", title="Website")
        make_project(session, "gi-blog", owner="General Informatics LLC", title="Blog")
        make_project(session, "acme-shop", owner="Acme", title="Shop")


def ask(body: str, thread: str | None = None) -> str:
    with session_scope() as session:
        message = threads.post_message(session, body, author="alice", source="web", thread=thread)
        return str(message.thread_id)


def route(*answers: dict[str, Any]) -> None:
    assert route_next(get_settings(), FakeModel(list(answers))) is not None


def started() -> list[str]:
    with session_scope() as session:
        return [t.project for t in session.scalars(select(Task).order_by(Task.created_at))]


def questions() -> list[Decision]:
    with session_scope() as session:
        found = session.scalars(
            select(Decision).where(
                Decision.kind == DecisionKind.ROUTE, Decision.status == DecisionStatus.PENDING
            )
        ).all()
        session.expunge_all()
        return list(found)


def unsure(project: str, *others: str, **fields: Any) -> dict[str, Any]:
    alternatives = [{"project": p, "confidence": 0.4} for p in others]
    return output(routed(project, 0.5, alternatives=alternatives, **fields))


def test_one_candidate_goes_there_without_asking() -> None:
    ask("Check the shop's latest orders")
    route(unsure("acme-shop"))
    assert started() == ["acme-shop"]
    assert questions() == []


def test_client_level_work_goes_to_the_client_s_only_project() -> None:
    ask("Read Acme's latest email")
    route(unsure("acme-shop", project_specific=False))
    assert started() == ["acme-shop"]


def test_client_level_work_goes_to_the_chosen_default_project() -> None:
    with session_scope() as session:
        clients.set_default_project(session, "general-informatics-llc", "gi-blog")
    ask("Read General Informatics' latest email and summarize it")
    route(unsure("gi-site", "gi-blog", project_specific=False))
    assert started() == ["gi-blog"]
    assert questions() == []


def test_without_a_default_it_asks_once_and_offers_to_set_one() -> None:
    ask("Read General Informatics' latest email and summarize it")
    route(unsure("gi-site", "gi-blog", project_specific=False))
    [question] = questions()
    payload = question.payload or {}
    assert payload["offer_default"] == "general-informatics-llc"
    assert payload["reason"] == "It could be for Website or Blog."  # plain, no numbers


def test_project_work_still_asks_and_says_why() -> None:
    why = "Both Website and Blog have a contact form."
    ask("Fix the contact form for General Informatics")
    route(unsure("gi-site", "gi-blog", why=why))
    [question] = questions()
    payload = question.payload or {}
    assert payload["reason"] == why
    assert "offer_default" not in payload  # a default wouldn't decide this
    with session_scope() as session:
        clients.set_default_project(session, "general-informatics-llc", "gi-blog")
    ask("Fix the other contact form for General Informatics")
    route(unsure("gi-site", "gi-blog", why=why))
    assert len(questions()) == 2  # project work isn't sent to the default


def test_the_answer_is_remembered_in_the_same_conversation() -> None:
    thread = ask("Fix the contact form for General Informatics")
    route(unsure("gi-site", "gi-blog"))
    [question] = questions()
    with session_scope() as session:
        inbox.answer(
            session, get_settings(), str(question.id), Answer(choice="gi-blog"), by="alice",
            via=Via.API,
        )  # fmt: skip
    ask("Now make its button blue", thread=thread)
    route(unsure("gi-site", "gi-blog", prompt="Make the contact form's button blue."))
    assert started() == ["gi-blog", "gi-blog"]
    assert questions() == []
    ask("Make the button blue on General Informatics' site too")  # a new conversation
    route(unsure("gi-site", "gi-blog"))
    assert len(questions()) == 1


def test_a_connection_only_one_of_the_client_s_projects_has_decides_it() -> None:
    """From the self-test: "Read Demo Co's newest email" asked which project, though only
    one of Demo Co's projects has the mail connection the task was to use."""
    with session_scope() as session:
        make_instance(session, "gi-mail", type=InstanceType.MCP, owner="General Informatics LLC")
    bind("gi-site", "gi-mail")
    read = "Using the gi-mail connection, report the newest email's subject."
    ask("Read General Informatics' newest email and tell me its subject")
    route(output(routed("gi-site", 0.85, prompt=read)))
    assert started() == ["gi-site"]
    assert questions() == []
    bind("gi-blog", "gi-mail")  # both have it: which one is still the operator's call
    ask("Read General Informatics' newest email and tell me its subject")
    route(output(routed("gi-site", 0.85, prompt=read)))
    assert len(questions()) == 1


def test_a_default_must_be_the_client_s_own_project() -> None:
    from ringkeeper.errors import RuleViolationError  # noqa: PLC0415

    with session_scope() as session, pytest.raises(RuleViolationError, match="isn't one of"):
        clients.set_default_project(session, "general-informatics-llc", "acme-shop")
    with session_scope() as session:
        assert session.get_one(Client, "general-informatics-llc").default_project is None
