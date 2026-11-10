"""The router never holds the database while the model thinks: it claims a message in a short
write, asks the model with the database read only, then applies the answer in a short write.
On SQLite a write holds the whole database (BEGIN IMMEDIATE), so anything longer would make
every other writer wait, and fail after the busy timeout."""

import threading
import time
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Message, MessageRole, Task
from ringkeeper.router.model import Completion
from ringkeeper.router.service import route_next
from ringkeeper.services import threads
from tests.fake_router import FakeModel, output, routed
from tests.helpers import make_project


class SlowModel(FakeModel):
    """Waits for `go` before answering, and says when it has started."""

    def __init__(self, answers: list[object]) -> None:
        super().__init__(answers)  # type: ignore[arg-type]
        self.started = threading.Event()
        self.go = threading.Event()

    def complete(self, system: str, prompt: str, model: str) -> Completion:
        self.started.set()
        assert self.go.wait(20), "the test never let the model answer"
        return super().complete(system, prompt, model)


@pytest.fixture(autouse=True)
def project(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "gi-site", owner="General Informatics LLC")


def ask(body: str) -> str:
    with session_scope() as session:
        return str(threads.post_message(session, body, author="owner", source="web").id)


def routing_in_the_background(model: SlowModel) -> threading.Thread:
    worker = threading.Thread(target=route_next, args=(get_settings(), model), daemon=True)
    worker.start()
    assert model.started.wait(20), "the router never asked the model"
    return worker


def test_others_can_write_while_the_model_thinks() -> None:
    ask("Fix the footer")
    model = SlowModel([output(routed("gi-site"))])
    worker = routing_in_the_background(model)
    began = time.monotonic()
    ask("Another request, sent meanwhile")  # a write, while the model call is in flight
    assert time.monotonic() - began < 5  # not waiting on the router's transaction
    model.go.set()
    worker.join(20)
    with session_scope() as session:
        assert [t.project for t in session.scalars(select(Task))] == ["gi-site"]


def test_a_message_canceled_meanwhile_isn_t_applied() -> None:
    ident = ask("Fix the footer")
    model = SlowModel([output(routed("gi-site"))])
    worker = routing_in_the_background(model)
    with session_scope() as session:
        session.get_one(Message, uuid.UUID(ident)).canceled_at = datetime.now(UTC)
    model.go.set()
    worker.join(20)
    with session_scope() as session:
        assert session.scalars(select(Task)).all() == []
        replies = session.scalars(select(Message).where(Message.role != MessageRole.USER)).all()
        assert replies == []


def test_a_claimed_message_isn_t_taken_twice() -> None:
    ask("Fix the footer")
    model = SlowModel([output(routed("gi-site"))])
    worker = routing_in_the_background(model)
    assert route_next(get_settings(), FakeModel([])) is None  # nothing else to route
    model.go.set()
    worker.join(20)
