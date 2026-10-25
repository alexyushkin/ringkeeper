"""New requests from Telegram (RINGKEEPER_TELEGRAM_REQUESTS=1), typed and spoken, and the
notices that follow them: what the router did, and each task's end."""

import json
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import select, update

from ringkeeper import voice
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Message, Task, TaskStatus, Thread, VoiceConfirmation
from ringkeeper.router.service import route_next
from ringkeeper.services import threads
from ringkeeper.telegram import api as telegram_api
from ringkeeper.telegram import text
from ringkeeper.telegram.api import BotApi
from ringkeeper.telegram.bridge import EXPIRED, TEXT_OR_VOICE_REQUEST, Bridge
from tests.fake_router import FakeModel, output, routed
from tests.fake_telegram import BOT_TOKEN, ME, STRANGER, FakeStt, FakeTelegram

pytestmark = pytest.mark.usefixtures("task_registry")

HEARD = "add a line to acme-web's notes saying voice works"


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    fake = FakeTelegram()
    monkeypatch.setattr(telegram_api, "transport", httpx2.MockTransport(fake.handle))
    monkeypatch.setenv("RINGKEEPER_TELEGRAM_CHAT_IDS", json.dumps([ME]))
    monkeypatch.setenv("RINGKEEPER_TELEGRAM_REQUESTS", "1")
    return fake


@pytest.fixture
def stt(monkeypatch: pytest.MonkeyPatch) -> FakeStt:
    fake = FakeStt()
    monkeypatch.setattr(voice, "transport", httpx2.MockTransport(fake.handle))
    return fake


@pytest.fixture
def bridge(telegram: FakeTelegram, stt: FakeStt) -> Bridge:
    settings = get_settings()
    assert settings.telegram_requests
    return Bridge(BotApi(BOT_TOKEN, settings.telegram_api_url), settings)


def messages() -> list[Message]:
    with session_scope() as session:
        found = list(session.scalars(select(Message).order_by(Message.created_at)))
        session.expunge_all()
        return found


def confirm_button(telegram: FakeTelegram, label: str = "✅ Confirm") -> str:
    [row] = telegram.sent[-1]["reply_markup"]["inline_keyboard"]
    return next(b["callback_data"] for b in row if b["text"] == label)


def test_a_typed_request_goes_to_the_router(bridge: Bridge, telegram: FakeTelegram) -> None:
    telegram.text("How many customers has globex?")
    bridge.poll_once(0)
    [message] = messages()
    assert (message.body, message.source, message.input) == (
        "How many customers has globex?", "telegram", "text",
    )  # fmt: skip
    assert message.author == "telegram:42 (@alice)"
    assert message.routed_at is None  # queued for the router
    with session_scope() as session:
        assert session.get_one(Thread, message.thread_id).source == "telegram"
    assert telegram.sent[-1]["text"] == (
        f"Got it. Routing it now (thread {str(message.thread_id)[:8]})."
    )


def test_a_voice_request_is_confirmed_first(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt
) -> None:
    telegram.voice("v1")
    bridge.poll_once(0)
    sent = telegram.sent[-1]
    assert sent["text"] == f"I heard: {HEARD}\n\nSend this request?"
    assert messages() == []  # nothing until Confirm

    telegram.press(confirm_button(telegram))
    bridge.poll_once(0)
    [message] = messages()
    assert (message.body, message.source, message.input) == (HEARD, "telegram", "voice")
    assert telegram.callbacks[-1]["text"] == "Sent"
    [edit] = [e for e in telegram.edits if e["message_id"] == sent["message_id"]]
    assert edit["text"] == (
        f"I heard: {HEARD}\n✔ Sent. Routing it now (thread {str(message.thread_id)[:8]})."
    )
    with session_scope() as session:
        row = session.scalars(select(VoiceConfirmation)).one()
        assert (row.kind, row.status, row.transcript) == ("request", "confirmed", None)


def test_redo_and_expiry_send_nothing(bridge: Bridge, telegram: FakeTelegram, stt: FakeStt) -> None:
    telegram.voice("v1")
    bridge.poll_once(0)
    telegram.press(confirm_button(telegram, "🔁 Redo"))
    bridge.poll_once(0)
    assert telegram.edits[-1]["text"].endswith("🔁 Redo: send a new voice note.")

    telegram.voice("v2")
    bridge.poll_once(0)
    late = confirm_button(telegram)
    with session_scope() as session:
        session.execute(
            update(VoiceConfirmation).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    telegram.press(late)
    bridge.poll_once(0)
    assert telegram.edits[-1]["text"] == EXPIRED
    assert messages() == []
    with session_scope() as session:
        statuses = session.scalars(
            select(VoiceConfirmation.status).order_by(VoiceConfirmation.created_at)
        ).all()
        assert list(statuses) == ["redone", "expired"]


def test_strangers_still_get_nothing(bridge: Bridge, telegram: FakeTelegram, stt: FakeStt) -> None:
    telegram.text("count customers", chat=STRANGER)
    telegram.voice("v1", chat=STRANGER)
    bridge.poll_once(0)
    assert telegram.sent == []
    assert (telegram.downloads, stt.received, messages()) == ([], [], [])


def test_other_kinds_of_message_get_a_pointer(bridge: Bridge, telegram: FakeTelegram) -> None:
    telegram.updates.append(
        {
            "update_id": 1,
            "message": {
                "message_id": 5, "from": {"id": 42}, "chat": {"id": ME, "type": "private"},
                "sticker": {"file_id": "s1"},
            },
        }
    )  # fmt: skip
    bridge.poll_once(0)
    assert telegram.sent[-1]["text"] == TEXT_OR_VOICE_REQUEST
    assert messages() == []


def test_notices_say_what_the_router_did_and_when_tasks_end(
    bridge: Bridge, telegram: FakeTelegram
) -> None:
    telegram.text("add a line to acme-web's notes saying voice works")
    bridge.poll_once(0)
    secret_prompt = "Append 'voice works' to NOTES.md. Client detail: invoice 4471."
    model = FakeModel([output(routed("acme-web", kind="content", prompt=secret_prompt))])
    route_next(get_settings(), model)
    with session_scope() as session:
        task = session.scalars(select(Task)).one()
        task_id, thread = str(task.id), str(task.thread_id)[:8]

    assert bridge.send_notices() == 1
    assert telegram.sent[-1]["text"] == (
        f"Request {thread}:\n→ acme-web: task {task_id[:8]} (content)"
    )
    assert "invoice" not in telegram.sent[-1]["text"]  # never the worker prompt
    assert bridge.send_notices() == 0  # once only
    with session_scope() as session:
        session.execute(
            update(Task).values(status=TaskStatus.SUCCEEDED, finished_at=datetime.now(UTC))
        )
    assert bridge.send_notices() == 1
    assert telegram.sent[-1]["text"] == f"✔ acme-web · task {task_id[:8]} succeeded"
    assert bridge.send_notices() == 0


def test_a_request_with_nothing_to_do_gets_the_routers_reply(
    bridge: Bridge, telegram: FakeTelegram
) -> None:
    telegram.text("hello there")
    bridge.poll_once(0)
    route_next(get_settings(), FakeModel([output(reply="Hi! Nothing to do.")]))
    bridge.send_notices()
    assert telegram.sent[-1]["text"].endswith(":\nHi! Nothing to do.")


def test_a_long_router_reply_is_shown_in_full(bridge: Bridge, telegram: FakeTelegram) -> None:
    telegram.text("why?")
    bridge.poll_once(0)
    reply = ("I only sort requests into project tasks. " * 8).strip()  # ~330 characters
    route_next(get_settings(), FakeModel([output(reply=reply)]))
    bridge.send_notices()
    assert reply in telegram.sent[-1]["text"]
    assert text.SHORTENED not in telegram.sent[-1]["text"]


def test_requests_from_elsewhere_get_no_notices(bridge: Bridge, telegram: FakeTelegram) -> None:
    with session_scope() as session:
        threads.post_message(session, "count globex customers", author="alice", source="cli")
    route_next(get_settings(), FakeModel([output(routed("globex-portal"))]))
    with session_scope() as session:
        session.execute(update(Task).values(status=TaskStatus.FAILED))
    assert bridge.send_notices() == 0
    assert telegram.sent == []
