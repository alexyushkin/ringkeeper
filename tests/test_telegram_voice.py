"""Voice on Telegram against a mocked Bot API and stt service: confirm and redo,
expiry, the allowlist, limits, and that no audio is kept anywhere."""

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from sqlalchemy import select, text, update

from ringkeeper import voice
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import (
    AuditEvent,
    Decision,
    DecisionKind,
    DecisionStatus,
    TaskStatus,
    VoiceConfirmation,
)
from ringkeeper.services import inbox, tasks
from ringkeeper.telegram import api as telegram_api
from ringkeeper.telegram.api import BotApi
from ringkeeper.telegram.bridge import EXPIRED, NOT_A_PERSON, REQUESTS_OFF, Bridge
from tests.fake_telegram import AUDIO_MARKER, BOT_TOKEN, ME, STRANGER, FakeStt, FakeTelegram
from tests.helpers import table_text

pytestmark = pytest.mark.usefixtures("task_registry")

HEARD = "add a line to acme-web's notes saying voice works"


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    fake = FakeTelegram()
    monkeypatch.setattr(telegram_api, "transport", httpx2.MockTransport(fake.handle))
    monkeypatch.setenv("RINGKEEPER_TELEGRAM_CHAT_IDS", json.dumps([ME]))
    return fake


@pytest.fixture
def stt(monkeypatch: pytest.MonkeyPatch) -> FakeStt:
    fake = FakeStt()
    monkeypatch.setattr(voice, "transport", httpx2.MockTransport(fake.handle))
    return fake


@pytest.fixture
def bridge(telegram: FakeTelegram, stt: FakeStt) -> Bridge:
    settings = get_settings()
    return Bridge(BotApi(BOT_TOKEN, settings.telegram_api_url), settings)


def ask_question(b: Bridge, telegram: FakeTelegram) -> tuple[str, int]:
    """A worker's question, sent to Telegram. Returns its id and the Telegram message id."""
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="acme-web", prompt="go")
        task.sessions[0].sdk_session_id = "sdk-1"
        task.status = task.sessions[0].status = TaskStatus.NEEDS_INPUT
        question = inbox.create(
            session, get_settings(), DecisionKind.QUESTION,
            {"question": "What should the notes line say?", "role": "worker"},
            project="acme-web", task=task,
        )  # fmt: skip
        ident = str(question.id)
    b.send_new()
    return ident, telegram.sent[-1]["message_id"]


def buttons(sent: dict[str, Any]) -> dict[str, str]:
    [row] = sent["reply_markup"]["inline_keyboard"]
    return {button["text"]: button["callback_data"] for button in row}


def decision(ident: str) -> Decision:
    with session_scope() as session:
        found = session.get_one(Decision, ident)
        session.expunge(found)
        return found


def confirmations() -> list[VoiceConfirmation]:
    with session_scope() as session:
        rows = list(
            session.scalars(select(VoiceConfirmation).order_by(VoiceConfirmation.created_at))
        )
        session.expunge_all()
        return rows


def hear(b: Bridge, telegram: FakeTelegram, question_message: int, file_id: str = "v1") -> str:
    """Reply to the question with a voice note; returns the Confirm button's data."""
    telegram.voice(file_id, reply_to=question_message)
    b.poll_once(0)
    sent = telegram.sent[-1]
    assert sent["text"].startswith(f"I heard: {HEARD}")
    return buttons(sent)["✅ Confirm"]


def test_a_confirmed_voice_reply_answers_like_a_typed_one(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt
) -> None:
    question_id, message_id = ask_question(bridge, telegram)
    telegram.voice("v1", reply_to=message_id)
    bridge.poll_once(0)

    sent = telegram.sent[-1]
    assert sent["text"] == (f"I heard: {HEARD}\n\nAnswer question {question_id[:8]} with this?")
    assert list(buttons(sent)) == ["✅ Confirm", "🔁 Redo"]
    assert decision(question_id).status is DecisionStatus.PENDING  # nothing until Confirm
    [pending] = confirmations()
    assert (pending.kind, pending.status, pending.transcript) == ("answer", "pending", HEARD)
    assert pending.external_id == sent["message_id"]

    telegram.press(buttons(sent)["✅ Confirm"])
    bridge.poll_once(0)
    answered = decision(question_id)
    assert answered.status is DecisionStatus.ANSWERED
    assert answered.answer == {"text": HEARD, "input": "voice"}
    assert answered.answered_via == "telegram"
    assert telegram.callbacks[-1]["text"] == "Done: question answered"
    [edit] = [e for e in telegram.edits if e["message_id"] == sent["message_id"]]
    assert edit["text"] == f"I heard: {HEARD}\n✔ Confirmed"
    [done] = confirmations()
    assert (done.status, done.transcript) == ("confirmed", None)  # it lives on in the answer
    with session_scope() as session:
        task = tasks.get_task(session, str(answered.task_id))
        resumed = tasks.latest_session(task)
        assert resumed.sdk_session_id == "sdk-1"
        assert f"Answer: {HEARD}" in resumed.prompt
        audit = session.scalars(
            select(AuditEvent).where(AuditEvent.decision_id == answered.id)
        ).one()
        assert (audit.args or {})["input"] == "voice"

    telegram.press(buttons(sent)["✅ Confirm"])  # each confirmation works once
    bridge.poll_once(0)
    assert telegram.callbacks[-1]["text"] == "Already confirmed"


def test_redo_discards_the_transcript_and_a_new_voice_note_can_be_confirmed(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt
) -> None:
    question_id, message_id = ask_question(bridge, telegram)
    hear(bridge, telegram, message_id)
    telegram.press(buttons(telegram.sent[-1])["🔁 Redo"])
    bridge.poll_once(0)
    [redone] = confirmations()
    assert (redone.status, redone.transcript) == ("redone", None)
    assert decision(question_id).status is DecisionStatus.PENDING
    assert telegram.callbacks[-1]["text"] == "Send it again"
    assert "Redo" in telegram.edits[-1]["text"]

    confirm = hear(bridge, telegram, message_id, "v2")
    telegram.press(confirm)
    bridge.poll_once(0)
    assert decision(question_id).status is DecisionStatus.ANSWERED
    assert [c.status for c in confirmations()] == ["redone", "confirmed"]


def test_after_redo_the_next_voice_note_answers_the_same_question_without_a_reply(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt
) -> None:
    question_id, message_id = ask_question(bridge, telegram)
    hear(bridge, telegram, message_id)
    telegram.press(buttons(telegram.sent[-1])["🔁 Redo"])
    bridge.poll_once(0)
    assert "It will answer the same question." in telegram.edits[-1]["text"]

    telegram.voice("v2")  # not a Telegram reply
    bridge.poll_once(0)
    sent = telegram.sent[-1]
    assert sent["text"].endswith(f"Answer question {question_id[:8]} with this?")
    telegram.press(buttons(sent)["✅ Confirm"])
    bridge.poll_once(0)
    assert decision(question_id).status is DecisionStatus.ANSWERED

    telegram.voice("v3")  # the question is answered: back to the usual rules
    bridge.poll_once(0)
    assert telegram.sent[-1]["text"] == REQUESTS_OFF


def test_only_the_sender_can_confirm_their_voice_note(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt
) -> None:
    question_id, message_id = ask_question(bridge, telegram)
    confirm = hear(bridge, telegram, message_id)
    telegram.press(confirm, user=43)  # someone else in the same group
    bridge.poll_once(0)
    assert (
        telegram.callbacks[-1]["text"] == "Only the person who sent the voice note can confirm it"
    )
    assert decision(question_id).status is DecisionStatus.PENDING
    telegram.press(confirm)
    bridge.poll_once(0)
    assert decision(question_id).status is DecisionStatus.ANSWERED


def test_a_redo_follow_up_is_the_senders_and_works_once(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt
) -> None:
    question_id, message_id = ask_question(bridge, telegram)
    hear(bridge, telegram, message_id)
    telegram.press(buttons(telegram.sent[-1])["🔁 Redo"])
    bridge.poll_once(0)
    telegram.text("not my answer", user=43)  # someone else: not taken as the answer
    bridge.poll_once(0)
    assert telegram.sent[-1]["text"] == REQUESTS_OFF
    assert decision(question_id).status is DecisionStatus.PENDING
    telegram.voice("v2")  # the sender's follow-up uses the Redo
    bridge.poll_once(0)
    assert telegram.sent[-1]["text"].endswith(f"Answer question {question_id[:8]} with this?")
    assert [c.status for c in confirmations()] == ["used", "pending"]
    telegram.press(buttons(telegram.sent[-1])["🔁 Redo"])
    bridge.poll_once(0)
    telegram.voice("v3")  # a new Redo, a new follow-up
    bridge.poll_once(0)
    assert [c.status for c in confirmations()] == ["used", "used", "pending"]


@pytest.mark.parametrize(
    "fields",
    [
        {"user": 1087968824},  # an anonymous group admin
        {"sender_chat": {"id": -100123, "type": "channel"}},  # posted as a channel
    ],
)
def test_anonymous_senders_cant_answer(
    bridge: Bridge, telegram: FakeTelegram, fields: dict[str, Any]
) -> None:
    question_id, message_id = ask_question(bridge, telegram)
    telegram.text("deny it", reply_to_message={"message_id": message_id}, **fields)
    bridge.poll_once(0)
    assert telegram.sent[-1]["text"] == NOT_A_PERSON
    assert decision(question_id).status is DecisionStatus.PENDING


def test_bots_cant_press_buttons(bridge: Bridge, telegram: FakeTelegram, stt: FakeStt) -> None:
    question_id, message_id = ask_question(bridge, telegram)
    confirm = hear(bridge, telegram, message_id)
    telegram.press(confirm)
    telegram.updates[-1]["callback_query"]["from"]["is_bot"] = True
    bridge.poll_once(0)
    assert telegram.callbacks[-1]["text"] == NOT_A_PERSON
    assert decision(question_id).status is DecisionStatus.PENDING


def test_the_speech_model_gets_project_and_connection_names_as_a_hint(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt
) -> None:
    _, message_id = ask_question(bridge, telegram)
    hear(bridge, telegram, message_id)
    [hint] = stt.hints
    assert hint is not None
    names = hint.split(", ")
    assert {"acme-web", "globex-portal", "n8n", "Supabase"} <= set(names)


def test_confirmations_expire(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt, monkeypatch: pytest.MonkeyPatch
) -> None:
    question_id, message_id = ask_question(bridge, telegram)
    confirm = hear(bridge, telegram, message_id)
    [row] = confirmations()
    expires = row.expires_at - row.created_at
    assert timedelta(minutes=9) < expires <= timedelta(minutes=10, seconds=5)

    def age(minutes: int) -> None:
        with session_scope() as session:
            session.execute(
                update(VoiceConfirmation).values(
                    expires_at=datetime.now(UTC) - timedelta(minutes=minutes)
                )
            )

    # A late press: refused, and the message says so.
    age(1)
    telegram.press(confirm)
    bridge.poll_once(0)
    assert telegram.callbacks[-1]["text"] == "This expired; send it again."
    assert telegram.edits[-1]["text"] == EXPIRED
    assert decision(question_id).status is DecisionStatus.PENDING
    [expired] = confirmations()
    assert (expired.status, expired.transcript) == ("expired", None)

    # Nobody presses: the sweep closes it.
    hear(bridge, telegram, message_id, "v2")
    age(1)
    assert bridge.expire_confirmations() == 1
    assert telegram.edits[-1] == {
        "chat_id": ME, "message_id": telegram.sent[-1]["message_id"], "text": EXPIRED,
        "reply_markup": {"inline_keyboard": []}, "link_preview_options": {"is_disabled": True},
    }  # fmt: skip
    assert [c.transcript for c in confirmations()] == [None, None]
    assert bridge.expire_confirmations() == 0


def test_a_voice_reply_to_an_answered_question_is_not_transcribed(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt
) -> None:
    question_id, message_id = ask_question(bridge, telegram)
    with session_scope() as session:
        inbox.answer(session, get_settings(), question_id, inbox.Answer(text="typed"),
                     by="alice", via=inbox.Via.CLI)  # fmt: skip
    telegram.voice("v1", reply_to=message_id)
    bridge.poll_once(0)
    assert telegram.sent[-1]["text"] == "Not done: that question is already answered"
    assert (telegram.downloads, stt.received) == ([], [])


def test_confirming_after_the_question_was_answered_elsewhere(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt
) -> None:
    question_id, message_id = ask_question(bridge, telegram)
    confirm = hear(bridge, telegram, message_id)
    with session_scope() as session:
        inbox.answer(session, get_settings(), question_id, inbox.Answer(text="typed"),
                     by="alice", via=inbox.Via.CLI)  # fmt: skip
    telegram.press(confirm)
    bridge.poll_once(0)
    assert telegram.callbacks[-1]["text"].startswith("Not done:")
    assert "already answered" in telegram.callbacks[-1]["text"]
    assert decision(question_id).answer == {"text": "typed"}


def test_the_allowlist_covers_voice_notes_and_confirmations(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt
) -> None:
    _, message_id = ask_question(bridge, telegram)
    sent_before = len(telegram.sent)
    telegram.voice("v1", reply_to=message_id, chat=STRANGER)
    telegram.voice("v2", chat=STRANGER)
    bridge.poll_once(0)
    assert len(telegram.sent) == sent_before  # strangers get nothing
    assert (telegram.downloads, stt.received) == ([], [])

    confirm = hear(bridge, telegram, message_id)
    telegram.press(confirm, chat=STRANGER)
    bridge.poll_once(0)
    assert telegram.callbacks == []
    assert [c.status for c in confirmations()] == ["pending"]


def test_limits_are_explained_and_nothing_is_downloaded(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt
) -> None:
    _, message_id = ask_question(bridge, telegram)
    telegram.voice("long", seconds=252, reply_to=message_id)
    telegram.voice("big", size=6_200_000, reply_to=message_id)
    bridge.poll_once(0)
    assert telegram.sent[-2]["text"] == (
        "That voice note is 4:12; the limit is 3:00. Send a shorter one."
    )
    assert telegram.sent[-1]["text"].startswith("That voice note is too big (6.2 MB)")
    assert (telegram.downloads, stt.received, confirmations()) == ([], [], [])


def test_stt_down_is_explained(bridge: Bridge, telegram: FakeTelegram, stt: FakeStt) -> None:
    _, message_id = ask_question(bridge, telegram)
    stt.down = True
    telegram.voice("v1", reply_to=message_id)
    bridge.poll_once(0)
    assert telegram.sent[-1]["text"] == voice.UNREACHABLE
    assert confirmations() == []


def test_with_requests_off_messages_get_the_hint_and_voice_is_never_downloaded(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt
) -> None:
    telegram.text("please count customers")
    telegram.voice("v1")
    telegram.reply(12345, "a reply to nothing we sent")
    bridge.poll_once(0)
    assert [m["text"] for m in telegram.sent] == [REQUESTS_OFF] * 3
    assert "ringkeeper ask" not in REQUESTS_OFF
    assert (telegram.downloads, stt.received) == ([], [])
    with session_scope() as session:
        assert session.execute(text("SELECT count(*) FROM messages")).scalar_one() == 0


@pytest.fixture
def watched_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every temp file the process might make at an empty directory."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


def test_no_audio_is_kept_anywhere(
    bridge: Bridge, telegram: FakeTelegram, stt: FakeStt, watched_tmp: Path, data_dir: Path
) -> None:
    before = {p for p in data_dir.rglob("*") if p.is_file()}
    question_id, message_id = ask_question(bridge, telegram)
    confirm = hear(bridge, telegram, message_id)
    # A second voice note that fails at the stt service: dropped as well.
    stt.status, stt.error = 422, "undecodable"
    telegram.voice("v2", reply_to=message_id)
    bridge.poll_once(0)
    telegram.press(confirm)
    bridge.poll_once(0)
    assert decision(question_id).status is DecisionStatus.ANSWERED

    [first, second] = stt.received
    assert first.startswith(AUDIO_MARKER)
    assert second.startswith(AUDIO_MARKER)
    assert list(watched_tmp.iterdir()) == []
    assert {p for p in data_dir.rglob("*") if p.is_file()} == before
    with session_scope() as session:
        for table in ("voice_confirmations", "decisions", "messages", "audit_events",
                      "notifications", "sessions", "settings_kv"):  # fmt: skip
            rows = table_text(session, table)
            assert not any("OggS" in row for row in rows), table
