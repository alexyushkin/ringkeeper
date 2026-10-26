"""The Telegram bridge against a mocked Bot API: summaries, buttons, replies, the allowlist."""

import json
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from ringkeeper.cli.main import app
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import (
    AuditEvent,
    AuditOutcome,
    Decision,
    DecisionKind,
    DecisionStatus,
    InstanceType,
    Notification,
    Task,
    TaskKind,
    TaskStatus,
)
from ringkeeper.services import inbox, tasks
from ringkeeper.telegram import api as telegram_api
from ringkeeper.telegram.api import BotApi
from ringkeeper.telegram.bridge import Bridge, callback_data
from ringkeeper.vault import get_vault
from tests.fake_telegram import BOT_TOKEN, ME, STRANGER, FakeTelegram

pytestmark = pytest.mark.usefixtures("task_registry")


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    fake = FakeTelegram()
    monkeypatch.setattr(telegram_api, "transport", httpx2.MockTransport(fake.handle))
    monkeypatch.setenv("RINGKEEPER_TELEGRAM_CHAT_IDS", json.dumps([ME]))
    monkeypatch.setenv("RINGKEEPER_TELEGRAM_SUMMARY_CHARS", "60")
    get_vault().store(get_settings().telegram_bot_token_ref, BOT_TOKEN)
    return fake


def bridge() -> Bridge:
    settings = get_settings()
    return Bridge(BotApi(BOT_TOKEN, settings.telegram_api_url), settings)


def held_write() -> str:
    with session_scope() as session:
        decision = Decision(
            kind=DecisionKind.APPROVE, project="acme-web", session_id="s", token_id="t",
            instance="acme-supabase-prod", service=InstanceType.SUPABASE, tool="execute",
            args={"sql": "UPDATE customers SET plan = 'gold' WHERE email = 'vip@acme.test'"},
            args_hash="h", expires_at=datetime.now(UTC) + timedelta(hours=1),
        )  # fmt: skip
        session.add(decision)
        session.flush()
        return str(decision.id)


def feature_waiting_on_plan() -> tuple[str, str]:
    with session_scope() as session:
        task = tasks.create_task(
            session, get_settings(), project="acme-web", prompt="build it", kind=TaskKind.FEATURE
        )
        task.sessions[0].sdk_session_id = "sdk-arch"
        task.status = task.sessions[0].status = TaskStatus.WAITING
        decision = inbox.create(
            session, get_settings(), DecisionKind.PLAN,
            {
                "title": "Add format_plan() with tests",
                "plan": "SECRET PLAN TEXT: touch src/acme/plans.py, add tests/test_plans.py",
                "worker_prompt": "Write format_plan ...",
            },
            project="acme-web", task=task,
        )  # fmt: skip
        return str(task.id), str(decision.id)


def decision(ident: str) -> Decision:
    with session_scope() as session:
        found = session.get(Decision, ident)
        assert found is not None
        session.expunge(found)
        return found


def test_new_decisions_go_to_allowed_chats_as_short_scrubbed_summaries(
    telegram: FakeTelegram,
) -> None:
    held = held_write()
    _, plan = feature_waiting_on_plan()
    assert bridge().send_new() == 2
    assert bridge().send_new() == 0  # each decision is sent once

    assert {m["chat_id"] for m in telegram.sent} == {ME}
    write_msg, plan_msg = telegram.sent
    assert write_msg["text"].startswith("Prod write · acme-web · acme-supabase-prod\n")
    assert "'execute' on acme-supabase-prod: waits for your OK" in write_msg["text"]
    for text in (write_msg["text"], plan_msg["text"]):
        assert "UPDATE" not in text
        assert "vip@acme.test" not in text
        assert "SECRET PLAN TEXT" not in text
        assert "Write format_plan" not in text
        summary_line = text.splitlines()[1]
        assert len(summary_line) <= 60
    assert "Add format_plan() with tests" in plan_msg["text"]

    buttons = [b for row in write_msg["reply_markup"]["inline_keyboard"] for b in row]
    assert [b["text"] for b in buttons] == ["✅ Approve", "❌ Deny"]
    assert buttons[0]["callback_data"] == f"rk:{held}:0"
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)
    with session_scope() as session:
        notes = session.scalars(select(Notification)).all()
        assert {(str(n.decision_id), n.status) for n in notes} == {(held, "sent"), (plan, "sent")}


def test_route_decisions_get_one_button_per_candidate(telegram: FakeTelegram) -> None:
    with session_scope() as session:
        route = inbox.create(
            session, get_settings(), DecisionKind.ROUTE,
            {
                "message": "update the numbers",
                "candidates": [
                    {"project": "acme-web", "confidence": 0.5},
                    {"project": "globex-portal", "confidence": 0.4},
                ],
                "task": {"prompt": "p", "kind": "data", "risk": "read", "complexity": "low",
                         "outcome": "numbers updated"},
            },
        )  # fmt: skip
        route_id = str(route.id)
    bridge().send_new()
    [sent] = telegram.sent
    labels = [b["text"] for row in sent["reply_markup"]["inline_keyboard"] for b in row]
    assert labels == ["acme-web", "globex-portal", "None of these"]

    telegram.press(callback_data(decision(route_id), 1))
    bridge().poll_once(0)
    answered = decision(route_id)
    assert (answered.status, answered.project) == (DecisionStatus.ANSWERED, "globex-portal")
    assert answered.decided_by == "telegram:42 (@alice)"
    with session_scope() as session:
        [task] = session.scalars(select(Task)).all()
        assert task.project == "globex-portal"


def test_a_plan_is_approved_with_a_button(telegram: FakeTelegram) -> None:
    task_id, plan = feature_waiting_on_plan()
    b = bridge()
    b.send_new()
    telegram.press(f"rk:{plan}:0")
    assert b.poll_once(0) == 1

    assert decision(plan).status is DecisionStatus.APPROVED
    assert decision(plan).answered_via == "telegram"
    assert telegram.callbacks[-1]["text"] == "Done: plan approved"
    with session_scope() as session:
        assert tasks.get_task(session, task_id).status is TaskStatus.QUEUED  # architect resumes
        event = session.scalars(
            select(AuditEvent).where(AuditEvent.outcome == AuditOutcome.ANSWERED)
        ).one()
        assert event.args == {
            "via": "telegram", "by": "telegram:42 (@alice)", "kind": "plan", "choice": "approve",
        }  # fmt: skip
    # The message is edited to show the outcome, without buttons.
    [edit] = telegram.edits
    assert edit["reply_markup"] == {"inline_keyboard": []}
    assert "approved" in edit["text"]
    assert telegram.offsets[-1] is None
    b.poll_once(0)
    assert telegram.offsets[-1] == 2  # the offset moved past the handled update


def test_callbacks_and_replies_from_other_chats_are_ignored(telegram: FakeTelegram) -> None:
    held = held_write()
    b = bridge()
    b.send_new()
    telegram.press(f"rk:{held}:0", chat=STRANGER)
    telegram.reply(telegram.sent[0]["message_id"], "approve it", chat=STRANGER)
    assert b.poll_once(0) == 2
    pending = decision(held)
    assert pending.status is DecisionStatus.PENDING
    assert pending.answer is None
    assert telegram.callbacks == []  # nothing is said back to a stranger
    assert [m["chat_id"] for m in telegram.sent] == [ME]


def test_a_held_write_approved_on_telegram_waits_for_the_gateway(telegram: FakeTelegram) -> None:
    held = held_write()
    b = bridge()
    b.send_new()
    telegram.press(f"rk:{held}:0")
    b.poll_once(0)
    pending = decision(held)
    assert pending.status is DecisionStatus.PENDING
    assert pending.answer == {"choice": "approve", "by": "telegram:42 (@alice)"}
    assert "running" in telegram.edits[-1]["text"]
    b.close_finished()
    assert len(telegram.edits) == 1  # not edited again while it runs

    with session_scope() as session:
        row = session.get(Decision, held)
        assert row is not None
        row.status = DecisionStatus.EXECUTED
        row.decided_at = datetime.now(UTC)
        row.decided_by = "telegram:42 (@alice)"
    assert b.close_finished() == 1
    assert "executed" in telegram.edits[-1]["text"]


def test_a_question_is_answered_by_replying(telegram: FakeTelegram) -> None:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="acme-web", prompt="go")
        task.sessions[0].sdk_session_id = "sdk-1"
        task.status = task.sessions[0].status = TaskStatus.NEEDS_INPUT
        question = inbox.create(
            session, get_settings(), DecisionKind.QUESTION,
            {"question": "Which column holds the plan name?", "role": "worker"},
            project="acme-web", task=task,
        )  # fmt: skip
        question_id = str(question.id)
    b = bridge()
    b.send_new()
    [sent] = telegram.sent
    assert sent["reply_markup"]["force_reply"] is True
    assert "Which column holds the plan name?" in sent["text"]

    telegram.reply(sent["message_id"], "plan_name")
    b.poll_once(0)
    answered = decision(question_id)
    assert answered.status is DecisionStatus.ANSWERED
    assert answered.answer == {"text": "plan_name"}
    assert telegram.sent[-1]["text"] == "Done: question answered"


def test_plain_messages_get_a_hint(telegram: FakeTelegram) -> None:
    telegram.reply(12345, "please count customers")  # a reply to nothing we sent
    bridge().poll_once(0)
    assert telegram.sent[-1]["text"].startswith("Requests from Telegram are turned off")


def test_telegram_cli_login_and_chats(telegram: FakeTelegram) -> None:
    runner = CliRunner()
    login = runner.invoke(app, ["telegram", "login"], input=BOT_TOKEN + "\n")
    assert login.exit_code == 0, login.output
    assert "@ringkeeper_test_bot" in login.stdout
    assert BOT_TOKEN not in login.output
    telegram.reply(1, "hi")
    chats = runner.invoke(app, ["telegram", "chats"])
    assert chats.exit_code == 0, chats.output
    assert f"{ME}" in chats.stdout
    assert "yes" in chats.stdout
    bad = runner.invoke(app, ["telegram", "login"], input="not-a-token\n")
    assert bad.exit_code == 1


def test_network_errors_never_show_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {request.url}")

    monkeypatch.setattr(telegram_api, "transport", httpx2.MockTransport(broken))
    with pytest.raises(telegram_api.TelegramError) as raised:
        BotApi(BOT_TOKEN).get_me()
    assert BOT_TOKEN not in str(raised.value)
    assert "ConnectError" in str(raised.value)


@pytest.mark.parametrize("command", ["/start", "/start@ringkeeper_test_bot", "/start abc"])
def test_start_gets_a_welcome_not_the_hint(telegram: FakeTelegram, command: str) -> None:
    telegram.updates.append(
        {
            "update_id": 1,
            "message": {
                "message_id": 7, "from": {"id": 42}, "chat": {"id": ME, "type": "private"},
                "text": command,
            },
        }
    )  # fmt: skip
    bridge().poll_once(0)
    [sent] = telegram.sent
    assert sent["text"].startswith("Hi! This is Ringkeeper.")
    assert "ringkeeper ask" not in sent["text"]


def test_start_from_a_stranger_gets_nothing(telegram: FakeTelegram) -> None:
    telegram.updates.append(
        {"update_id": 1, "message": {"message_id": 7, "chat": {"id": STRANGER}, "text": "/start"}}
    )
    bridge().poll_once(0)
    assert telegram.sent == []


def test_a_bot_token_is_scrubbed_as_soon_as_a_client_is_made() -> None:
    from ringkeeper.scrub import scrub  # noqa: PLC0415

    token = "123456789:AAH" + "q" * 32
    BotApi(token, "http://127.0.0.1:9").close()
    assert token not in scrub(f"failed calling /bot{token}/getMe")
