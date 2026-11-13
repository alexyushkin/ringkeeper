"""New requests from Telegram (RINGKEEPER_TELEGRAM_REQUESTS=1), typed and spoken, and the
notices that follow them: what the router did, and each task's end."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import select, update

from ringkeeper import channels, voice
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Message, Project, Task, TaskStatus, Thread, VoiceConfirmation
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
    assert telegram.sent[-1]["text"] == "Got it. Looking into it…"  # a question
    telegram.text("add a line to acme-web's notes")
    bridge.poll_once(0)
    assert telegram.sent[-1]["text"] == "Got it. Working out which project it's for…"


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
    assert edit["text"] == (f"I heard: {HEARD}\n✔ Sent. Working out which project it's for…")
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
        session.scalars(select(Task)).one()  # one task was made

    assert bridge.send_notices() == 1
    assert telegram.sent[-1]["text"] == ("Your request:\n→ Acme web: started")
    assert "invoice" not in telegram.sent[-1]["text"]  # never the worker prompt
    assert bridge.send_notices() == 0  # once only
    with session_scope() as session:
        session.execute(
            update(Task).values(status=TaskStatus.SUCCEEDED, finished_at=datetime.now(UTC))
        )
    assert bridge.send_notices() == 1
    assert telegram.sent[-1]["text"] == "✔ Acme web: answered"
    assert bridge.send_notices() == 0


def test_a_request_with_nothing_to_do_gets_the_routers_reply(
    bridge: Bridge, telegram: FakeTelegram
) -> None:
    telegram.text("hello there")
    bridge.poll_once(0)
    route_next(get_settings(), FakeModel([output(reply="Hi! Nothing to do.")]))
    bridge.send_notices()
    assert telegram.sent[-1]["text"] == "Hi! Nothing to do."


def test_a_question_about_ringkeeper_gets_the_answer_the_app_shows(
    bridge: Bridge, telegram: FakeTelegram
) -> None:
    """From real use (on Slack): the app answered, the chat got "Nothing to do."."""
    telegram.text("how many connections and clients do we currently have?")
    bridge.poll_once(0)
    assert telegram.sent[-1]["text"] == "Got it. Looking into it…"
    model = FakeModel([{"tasks": [], "about_ringkeeper": True}, "13 connections and 4 clients."])
    route_next(get_settings(), model)
    assert bridge.send_notices() == 1
    assert telegram.sent[-1]["text"] == "13 connections and 4 clients."


def test_a_long_router_reply_is_shown_in_full(bridge: Bridge, telegram: FakeTelegram) -> None:
    telegram.text("why?")
    bridge.poll_once(0)
    reply = ("I only sort requests into project tasks. " * 8).strip()  # ~330 characters
    route_next(get_settings(), FakeModel([output(reply=reply)]))
    bridge.send_notices()
    assert reply in telegram.sent[-1]["text"]
    assert text.SHORTENED not in telegram.sent[-1]["text"]


def test_notices_go_only_to_the_chat_the_request_came_from(
    bridge: Bridge, telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = 2002  # another allowed chat (a teammate)
    monkeypatch.setenv("RINGKEEPER_TELEGRAM_CHAT_IDS", json.dumps([ME, other]))
    both = Bridge(BotApi(BOT_TOKEN, get_settings().telegram_api_url), get_settings())
    telegram.text("count globex customers", chat=other)
    both.poll_once(0)
    route_next(get_settings(), FakeModel([output(routed("globex-portal"))]))
    sent_before = len(telegram.sent)
    assert both.send_notices() == 1
    assert [m["chat_id"] for m in telegram.sent[sent_before:]] == [other]


def test_links_are_defanged_and_never_previewed(bridge: Bridge, telegram: FakeTelegram) -> None:
    telegram.text("hello there")
    bridge.poll_once(0)
    reply = "See https://evil.example/login?next=x or http://10.0.0.5:8080/admin."
    route_next(get_settings(), FakeModel([output(reply=reply)]))
    bridge.send_notices()
    sent = telegram.sent[-1]
    assert "https://" not in sent["text"]
    assert "hxxps://evil[.]example/login?next=x" in sent["text"]
    assert "hxxp://10[.]0[.]0[.]5:8080/admin" in sent["text"]
    assert sent["link_preview_options"] == {"is_disabled": True}


def test_requests_from_elsewhere_get_no_notices(bridge: Bridge, telegram: FakeTelegram) -> None:
    with session_scope() as session:
        threads.post_message(session, "count globex customers", author="alice", source="cli")
    route_next(get_settings(), FakeModel([output(routed("globex-portal"))]))
    with session_scope() as session:
        session.execute(update(Task).values(status=TaskStatus.FAILED))
    assert bridge.send_notices() == 0
    assert telegram.sent == []


def test_a_routine_set_to_send_a_summary_sends_it_once(
    bridge: Bridge, telegram: FakeTelegram
) -> None:
    from ringkeeper.services import agents, routines  # noqa: PLC0415
    from ringkeeper.services.agents import AgentIn  # noqa: PLC0415
    from ringkeeper.services.routines import RoutineIn  # noqa: PLC0415

    assert bridge.send_new() == 0  # Telegram's first look: from now on
    with session_scope() as session:
        agents.create_agent(session, "acme-web", AgentIn("digest"))
        made = routines.create_routine(
            session, "acme-web",
            RoutineIn("Morning digest", "digest", "Digest", "manual", notify="channels"),
        )  # fmt: skip
        started = routines.start_run(session, get_settings(), made, "manual")
        assert started.task is not None
        started.task.status = TaskStatus.SUCCEEDED
        started.task.finished_at = datetime.now(UTC)
        started.task.summary = "3 new leads; nothing failed."
    assert bridge.send_new() == 1
    assert telegram.sent[-1]["text"] == (
        "✔ Routine Morning digest · Acme web: answered\n3 new leads; nothing failed."
    )
    assert bridge.send_new() == 0


def test_telegram_requests_continue_one_conversation(
    bridge: Bridge, telegram: FakeTelegram
) -> None:
    telegram.text("count globex customers")
    bridge.poll_once(0)
    telegram.text("now do it for acme")
    bridge.poll_once(0)
    assert telegram.sent[-1]["text"] == "Got it. Working out which project it's for…"
    first, second = messages()
    assert first.thread_id == second.thread_id
    telegram.text("new")
    bridge.poll_once(0)
    assert telegram.sent[-1]["text"] == threads.STARTED_NEW
    telegram.text("count acme customers")
    bridge.poll_once(0)
    assert len({m.thread_id for m in messages()}) == 2


def test_a_telegram_link_s_address_reaches_the_request(
    bridge: Bridge, telegram: FakeTelegram
) -> None:
    telegram.text(
        "count the orders in the sheet",
        entities=[{"type": "text_link", "offset": 24, "length": 5, "url": "https://x.example/s"}],
    )
    bridge.poll_once(0)
    [request] = [m.body for m in messages() if m.author != "ringkeeper" and m.role.value == "user"]
    assert request == "count the orders in the sheet (https://x.example/s)"


def test_a_telegram_task_s_files_come_with_its_result(
    bridge: Bridge, telegram: FakeTelegram, tmp_path: Path
) -> None:
    (tmp_path / "orders.csv").write_bytes(b"id,total\n1,40\n")
    telegram.text("export the open orders")
    bridge.poll_once(0)
    route_next(get_settings(), FakeModel([output(routed("acme-web"))]))
    bridge.send_notices()
    with session_scope() as session:
        session.scalars(
            select(Project).where(Project.name == "acme-web")
        ).one().workspace_path = str(tmp_path)
        session.execute(
            update(Task).values(
                status=TaskStatus.SUCCEEDED, finished_at=datetime.now(UTC),
                summary="Exported 1 order.", changed_files=["orders.csv"],
            )
        )  # fmt: skip
    bridge.send_notices()
    assert telegram.sent[-1]["text"].startswith("✔ Acme web: done\nExported 1 order.")
    [(name, raw)] = telegram.documents
    assert name == "orders.csv"
    assert b"id,total" in raw
    assert f'name="chat_id"\r\n\r\n{ME}'.encode() in raw


def test_telegram_with_full_results_off_sends_no_result_or_files(
    bridge: Bridge, telegram: FakeTelegram, tmp_path: Path
) -> None:
    (tmp_path / "orders.csv").write_bytes(b"id,total\n1,40\n")
    with session_scope() as session:
        channels.set_telegram_prefs(session, {"full_results": False})
    telegram.text("export the open orders")
    bridge.poll_once(0)
    route_next(get_settings(), FakeModel([output(routed("acme-web"))]))
    bridge.send_notices()
    with session_scope() as session:
        session.scalars(
            select(Project).where(Project.name == "acme-web")
        ).one().workspace_path = str(tmp_path)
        session.execute(
            update(Task).values(
                status=TaskStatus.SUCCEEDED, finished_at=datetime.now(UTC),
                summary="Exported 1 order.", changed_files=["orders.csv"],
            )
        )  # fmt: skip
    bridge.send_notices()
    said = telegram.sent[-1]["text"]
    assert said.startswith("✔ Acme web: done")
    assert "Exported 1 order." not in said
    assert telegram.documents == []


def test_a_telegram_task_ends_with_its_result(bridge: Bridge, telegram: FakeTelegram) -> None:
    telegram.text("list the open orders")
    bridge.poll_once(0)
    route_next(get_settings(), FakeModel([output(routed("acme-web"))]))
    bridge.send_notices()
    with session_scope() as session:
        session.execute(
            update(Task).values(
                status=TaskStatus.SUCCEEDED, finished_at=datetime.now(UTC),
                summary="| Order | Total |\n|---|---|\n| 1007 | $40 |",
            )
        )  # fmt: skip
    bridge.send_notices()
    assert telegram.sent[-1]["text"].startswith("✔ Acme web: answered\nOrder · Total\n1007 · $40")
