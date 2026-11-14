"""A request with no reply after 10 seconds shows Retry: it wakes the router and changes
nothing, so a request already on its way isn't routed twice."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Message, Task
from ringkeeper.router.service import route_next
from ringkeeper.services import threads
from ringkeeper.storage import events
from tests import test_web_api
from tests.fake_router import FakeModel, output, routed
from tests.helpers import make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

pytestmark = pytest.mark.usefixtures("task_registry")


@pytest.fixture(autouse=True)
def project(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "hb-site", owner="Harbor Bakery", title="Bakery site")


def ask(body: str) -> str:
    with session_scope() as session:
        return str(threads.post_message(session, body, author="alice", source="cli").id)


def woken(owner: TestClient, ident: str) -> bool:
    """Whether Retry on this message woke the router."""
    listening = events.subscribe(get_settings().database_url, [threads.MESSAGES_CHANNEL])
    try:
        answered = owner.post(f"/messages/{ident}/nudge")
        assert answered.status_code == 200, answered.text
        return bool(listening.get(timeout=0.5))
    finally:
        listening.close()


def test_retry_wakes_the_router_and_changes_nothing(owner: TestClient) -> None:
    ident = ask("Find the emails from the flour supplier")
    assert woken(owner, ident)
    with session_scope() as session:
        message = session.get_one(Message, uuid.UUID(ident))
        assert message.routing is None
        assert message.routed_at is None
    route_next(get_settings(), FakeModel([output(routed("hb-site"))]))
    with session_scope() as session:
        assert [t.project for t in session.scalars(select(Task))] == ["hb-site"]  # once


def test_a_request_with_its_reply_isnt_routed_again(owner: TestClient) -> None:
    ident = ask("Find the emails from the flour supplier")
    route_next(get_settings(), FakeModel([output(routed("hb-site"))]))
    assert not woken(owner, ident)
    assert route_next(get_settings(), FakeModel([])) is None
    with session_scope() as session:
        assert len(session.scalars(select(Task)).all()) == 1


def test_an_unknown_message(owner: TestClient) -> None:
    assert owner.post(f"/messages/{uuid.uuid4()}/nudge").status_code == 404
