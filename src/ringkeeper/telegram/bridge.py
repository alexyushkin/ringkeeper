"""The Telegram bridge: send each new decision, take answers from buttons and replies.

Telegram bot chats aren't end-to-end encrypted, so this is a notification
channel only. A notification carries the kind, project, instance and a short
scrubbed one-line description (`telegram_summary_chars`), plus buttons. Never
SQL, arguments, diffs or plan text: full details stay in the CLI and API.

Only chats in `telegram_chat_ids` get notifications, and updates from any
other chat are ignored. Every answer is audited as via=telegram with the
Telegram user who pressed the button or replied.

A voice reply to a question is transcribed by the stt service and echoed as
"I heard: ..." with Confirm and Redo; Confirm answers exactly like a typed
reply. Audio is never stored, only the transcript, which is cleared once the
confirmation is resolved or expires (`telegram_confirm_minutes`).

With `telegram_requests` on, other messages are new requests: typed ones go
straight to the router (the same path as `ringkeeper ask`), voice notes after
the same "I heard" step. Their threads then get two kinds of short notices:
what the router did, and each task's end (project, id, status; results stay
in the CLI and API). With it off, they get a hint and nothing runs.
"""

import logging
import threading
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.listen import start_listener
from ringkeeper.models import (
    TERMINAL_STATUSES,
    Decision,
    DecisionKind,
    DecisionStatus,
    Message,
    MessageRole,
    Notification,
    Setting,
    Task,
    TaskStatus,
    Thread,
    VoiceConfirmation,
)
from ringkeeper.scrub import scrub
from ringkeeper.services import inbox, threads
from ringkeeper.services.decisions import DECISIONS_CHANNEL
from ringkeeper.storage.sql import insert
from ringkeeper.telegram import text as telegram_text
from ringkeeper.telegram.api import BotApi, TelegramError
from ringkeeper.voice import SttClient, VoiceError, VoiceNote, vocabulary
from ringkeeper.voice import transcribe as transcribe_voice

logger = logging.getLogger(__name__)

CHANNEL = "telegram"
OFFSET_KEY = "telegram.offset"
CALLBACK_PREFIX = "rk"
CONFIRM_PREFIX = "rv"  # 'rv:<confirmation uuid>:c' (Confirm) or ':r' (Redo)
CONFIRM, REDO = "c", "r"
ANSWER, REQUEST = "answer", "request"  # what a confirmed transcript becomes
HEARD_CHARS = 3500  # Telegram caps a message at 4096 characters
# sent -> running (an approved held write the gateway hasn't run yet) -> closed
SENT, RUNNING, CLOSED, FAILED = "sent", "running", "closed", "failed"
KIND_LABELS = {
    DecisionKind.ROUTE: "Route",
    DecisionKind.APPROVE: "Prod write",
    DecisionKind.PLAN: "Plan",
    DecisionKind.QUESTION: "Question",
}
BUTTON_LABELS = {"approve": "✅ Approve", "deny": "❌ Deny", inbox.NO_PROJECT: "None of these"}
WELCOME = (
    "Hi! This is Ringkeeper. Decisions that need you will show up here: "
    "press a button, or reply to a question."
)
REQUESTS_OFF = (
    "Requests from Telegram are turned off on this install. Turn on "
    "RINGKEEPER_TELEGRAM_REQUESTS to send requests here, or use the Ringkeeper app."
)
TEXT_OR_VOICE = "Reply to the question with text or a voice note."
TEXT_OR_VOICE_REQUEST = "Send a request as a text message or a voice note."
EXPIRED = "⌛ Expired. Send it again."


def callback_data(decision: Decision, index: int) -> str:
    """At most 64 bytes, as Telegram requires: 'rk:<uuid>:<option index>'."""
    return f"{CALLBACK_PREFIX}:{decision.id}:{index}"


def render(decision: Decision, settings: Settings) -> str:
    head = [KIND_LABELS[decision.kind]]
    if decision.project:
        head.append(decision.project)
    if decision.instance:
        head.append(decision.instance)
    shown = telegram_text.body(
        decision, settings.telegram_summary_chars, settings.telegram_question_chars
    )
    lines = [" · ".join(head), shown.text, f"id {str(decision.id)[:8]}"]
    if shown.cut:
        lines.append(telegram_text.SHORTENED)
    if decision.kind is DecisionKind.QUESTION:
        lines.append("Reply to this message with your answer.")
    if why := inbox.destructive_reason(decision):
        lines.append(f"This write {why}: approve it in the Ringkeeper app. You can deny it here.")
    return scrub("\n".join(lines))


def keyboard(decision: Decision) -> dict[str, Any]:
    if decision.kind is DecisionKind.QUESTION:
        return {"force_reply": True, "input_field_placeholder": "Your answer"}
    # A write that deletes or drops data can only be denied here (approve it in the app).
    blocked = {"approve"} if inbox.destructive_reason(decision) else set[str]()
    buttons = [
        {"text": BUTTON_LABELS.get(option, option), "callback_data": callback_data(decision, i)}
        for i, option in enumerate(inbox.options(decision))
        if option not in blocked
    ]
    return {"inline_keyboard": [buttons[i : i + 2] for i in range(0, len(buttons), 2)]}


def outcome_line(decision: Decision) -> str:
    who = decision.decided_by or (decision.answer or {}).get("by") or "someone"
    choice = (decision.answer or {}).get("choice")
    match decision.status:
        case DecisionStatus.PENDING:
            return f"⏳ approved by {who}; running"
        case DecisionStatus.EXPIRED:
            return "⌛ expired"
        case DecisionStatus.ANSWERED if choice:
            return f"✔ {choice} (by {who})"
        case _:
            return f"✔ {decision.status} (by {who})"


def routed_text(session: Session, message: Message, limit: int) -> str:
    """Projects, task ids and kinds only: never the worker prompt or outcome."""
    head = f"Request {str(message.thread_id)[:8]}"
    if message.route_error:
        return f"{head}: I couldn't route it."
    routing = message.routing or {}
    lines: list[str] = []
    for ident in routing.get("tasks", []):
        task = session.get(Task, ident)
        if task is not None:
            lines.append(f"→ {task.project}: task {ident[:8]} ({task.kind or 'task'})")
    for ident in routing.get("decisions", []):
        lines.append(f"? which project: decision {ident[:8]}, sent separately")
    if not lines:
        # The router answered instead of making tasks: meant to be read, so it gets
        # the room a question gets, with the same rules (no SQL or code).
        output = cast(dict[str, Any], routing.get("output") or {})
        shown = telegram_text.question(str(output.get("reply") or "Nothing to do."), limit)
        lines.append(shown.text)
        if shown.cut:
            lines.append(telegram_text.SHORTENED)
    return "\n".join([f"{head}:", *lines])


def finished_text(task: Task) -> str:
    mark = "✔" if task.status is TaskStatus.SUCCEEDED else "✖"
    return f"{mark} {task.project} · task {str(task.id)[:8]} {task.status}"


def heard(transcript: str, question: str = "") -> str:
    shown = transcript if len(transcript) <= HEARD_CHARS else transcript[:HEARD_CHARS] + "…"
    return f"I heard: {shown}" + (f"\n\n{question}" if question else "")


def _close_confirmation(row: VoiceConfirmation, status: str) -> None:
    # The transcript lives on in the answer or message it became, if any.
    row.status, row.transcript, row.resolved_at = status, None, datetime.now(UTC)


# Telegram's stand-ins for people it hides: anonymous group admins, channels,
# and its own service account. A message from one of them isn't from a person.
ANONYMOUS_SENDERS = frozenset({1087968824, 136817688, 777000})
NOT_A_PERSON = "Send this from your own account (not anonymously or as a channel)."


def _person(user: dict[str, Any], message: dict[str, Any] | None = None) -> bool:
    """A real person sent it: not a bot, an anonymous admin or a channel."""
    if message is not None and message.get("sender_chat"):
        return False
    ident = user.get("id")
    return isinstance(ident, int) and not user.get("is_bot") and ident not in ANONYMOUS_SENDERS


def _who(user: dict[str, Any]) -> str:
    handle = user.get("username")
    return f"telegram:{user.get('id')}" + (f" (@{handle})" if handle else "")


class Bridge:
    def __init__(self, api: BotApi, settings: Settings | None = None) -> None:
        self.api = api
        self.settings = settings or get_settings()
        self.chats = frozenset(self.settings.telegram_chat_ids)

    # --- Outgoing ---

    def _unsent(self, session: Session) -> Sequence[Decision]:
        # Non-null only: NOT IN over a NULL matches nothing.
        sent = select(Notification.decision_id).where(
            Notification.channel == CHANNEL, Notification.decision_id.is_not(None)
        )
        return session.scalars(
            select(Decision)
            .where(Decision.status == DecisionStatus.PENDING, Decision.id.not_in(sent))
            .order_by(Decision.created_at)
        ).all()

    def send_new(self) -> int:
        """Send every pending decision not sent yet, to every allowed chat."""
        sent = 0
        with session_scope() as session:
            inbox.list_pending(session)  # expires stale ones first
            for decision in self._unsent(session):
                text = render(decision, self.settings)
                for chat in sorted(self.chats):
                    note = Notification(decision_id=decision.id, channel=CHANNEL, chat_id=chat)
                    try:
                        note.external_id = self.api.send_message(chat, text, keyboard(decision))
                        note.status = SENT
                        sent += 1
                    except TelegramError as error:
                        note.status, note.error = FAILED, str(error)
                    session.add(note)
                session.flush()
        return sent

    def close_finished(self) -> int:
        """Edit sent notifications whose decision has moved on, and drop their buttons."""
        closed = 0
        with session_scope() as session:
            rows = session.execute(
                select(Notification, Decision)
                .join(Decision, Decision.id == Notification.decision_id)
                .where(Notification.channel == CHANNEL, Notification.status.in_([SENT, RUNNING]))
            ).all()
            for note, decision in rows:
                running = decision.status is DecisionStatus.PENDING and bool(decision.answer)
                if decision.status is DecisionStatus.PENDING and not running:
                    continue
                if note.external_id is None or (running and note.status == RUNNING):
                    continue
                text = f"{render(decision, self.settings)}\n"
                text += outcome_line(decision)
                try:
                    self.api.edit_message(note.chat_id, note.external_id, scrub(text))
                except TelegramError as error:
                    logger.warning("could not update Telegram message: %s", error)
                if running:
                    note.status = RUNNING
                else:
                    note.status = CLOSED
                    closed += 1
        return closed

    def _notify(
        self, session: Session, text: str, thread_id: uuid.UUID | None, **subject: uuid.UUID
    ) -> int:
        """News about a request: only to the chat it came from (if still allowed)."""
        thread = session.get(Thread, thread_id) if thread_id is not None else None
        origin = thread.origin_chat_id if thread is not None else None
        chats = {origin} & self.chats if origin is not None else self.chats
        sent = 0
        for chat in sorted(chats):
            note = Notification(channel=CHANNEL, chat_id=chat, **subject)
            try:
                note.external_id = self.api.send_message(chat, scrub(text))
                note.status = CLOSED  # nothing to update later
                sent += 1
            except TelegramError as error:
                note.status, note.error = FAILED, str(error)
            session.add(note)
        session.flush()
        return sent

    def send_notices(self) -> int:
        """For requests from Telegram: what the router did, and when each task ends."""
        sent = 0
        with session_scope() as session:
            noticed = select(Notification.message_id).where(
                Notification.channel == CHANNEL, Notification.message_id.is_not(None)
            )
            routed = session.scalars(
                select(Message)
                .where(
                    Message.source == CHANNEL,
                    Message.role == MessageRole.USER,
                    Message.routed_at.is_not(None),
                    Message.id.not_in(noticed),
                )
                .order_by(Message.created_at)
            ).all()
            for message in routed:
                text = routed_text(session, message, self.settings.telegram_question_chars)
                sent += self._notify(session, text, message.thread_id, message_id=message.id)
            ended = select(Notification.task_id).where(
                Notification.channel == CHANNEL, Notification.task_id.is_not(None)
            )
            finished = session.scalars(
                select(Task)
                .join(Thread, Thread.id == Task.thread_id)
                .where(
                    Thread.source == CHANNEL,
                    Task.status.in_(list(TERMINAL_STATUSES)),
                    Task.id.not_in(ended),
                )
                .order_by(Task.finished_at)
            ).all()
            for task in finished:
                sent += self._notify(session, finished_text(task), task.thread_id, task_id=task.id)
        return sent

    # --- Incoming ---

    def handle(self, update: dict[str, Any]) -> None:
        if callback := update.get("callback_query"):
            self._on_callback(cast(dict[str, Any], callback))
        elif message := update.get("message"):
            self._on_message(cast(dict[str, Any], message))

    def _allowed(self, chat: dict[str, Any] | None) -> bool:
        return chat is not None and chat.get("id") in self.chats

    def _answer(self, ident: str, reply: inbox.Answer, user: dict[str, Any]) -> str:
        try:
            with session_scope() as session:
                answered = inbox.answer(
                    session, self.settings, ident, reply, by=_who(user), via=inbox.Via.TELEGRAM
                )
                return f"Done: {answered.decision.kind} {answered.decision.status}"
        except RingkeeperError as error:
            return f"Not done: {error}"

    def _on_callback(self, callback: dict[str, Any]) -> None:
        message = cast(dict[str, Any], callback.get("message") or {})
        if not self._allowed(cast(dict[str, Any] | None, message.get("chat"))):
            return  # not ours to answer, and nothing is said back
        parts = str(callback.get("data", "")).split(":")
        user = cast(dict[str, Any], callback.get("from") or {})
        chat_id = int(cast(dict[str, Any], message["chat"])["id"])
        feedback = "Unknown button"
        if not _person(user):
            feedback = NOT_A_PERSON
        elif len(parts) == 3 and parts[0] == CALLBACK_PREFIX:
            feedback = self._press(parts[1], parts[2], user)
        elif len(parts) == 3 and parts[0] == CONFIRM_PREFIX:
            feedback = self._resolve(parts[1], parts[2], chat_id, user)
        try:
            self.api.answer_callback(str(callback["id"]), scrub(feedback))
        except TelegramError as error:
            logger.warning("could not answer a Telegram callback: %s", error)
        self.close_finished()

    def _press(self, ident: str, index: str, user: dict[str, Any]) -> str:
        try:
            uuid.UUID(ident)
            with session_scope() as session:
                decision = session.get(Decision, ident)
                options = inbox.options(decision) if decision is not None else []
            choice = options[int(index)]
        except (ValueError, IndexError):
            return "This button is out of date"
        return self._answer(ident, inbox.Answer(choice=choice), user)

    def _on_message(self, message: dict[str, Any]) -> None:
        chat = cast(dict[str, Any] | None, message.get("chat"))
        if not self._allowed(chat):
            return
        chat_id = int(cast(dict[str, Any], chat)["id"])
        replied = cast(dict[str, Any] | None, message.get("reply_to_message"))
        text = str(message.get("text") or "").strip()
        note = VoiceNote.from_message(message)
        user = cast(dict[str, Any], message.get("from") or {})
        if not replied and text.split("@")[0].split(" ")[0] == "/start":
            self._say(chat_id, WELCOME)
            return
        if not _person(user, message):
            self._say(chat_id, NOT_A_PERSON)  # who answered or asked must be on record
            return
        decision_id = self._question_for(chat_id, replied) if replied else None
        if decision_id is None and not replied:
            decision_id = self._take_redo(chat_id, int(user["id"]))
        if decision_id is None:
            # Not an answer: a new request, if requests from Telegram are on.
            if not self.settings.telegram_requests:
                self._say(chat_id, REQUESTS_OFF)  # and a voice note is never downloaded
            elif note is not None:
                self._hear(chat_id, user, note, None)
            elif text:
                self._say(chat_id, self._request(text, user, "text", chat_id))
            else:
                self._say(chat_id, TEXT_OR_VOICE_REQUEST)
        elif note is not None:
            self._hear(chat_id, user, note, decision_id)
        elif text:
            self._say(chat_id, self._answer(decision_id, inbox.Answer(text=text), user))
            self.close_finished()
        else:
            self._say(chat_id, TEXT_OR_VOICE)

    def _question_for(self, chat_id: int, replied: dict[str, Any]) -> str | None:
        with session_scope() as session:
            note = session.scalars(
                select(Notification)
                .join(Decision, Decision.id == Notification.decision_id)
                .where(
                    Notification.channel == CHANNEL,
                    Notification.chat_id == chat_id,
                    Notification.external_id == replied.get("message_id"),
                    Decision.kind == DecisionKind.QUESTION,
                )
            ).one_or_none()
            return str(note.decision_id) if note else None

    # --- Voice: "I heard: ..." with Confirm and Redo ---

    def _take_redo(self, chat_id: int, user_id: int) -> str | None:
        """After Redo on an answer, the same person's next message in that chat
        answers the same question (within the confirmation window, while it's
        still open), no Telegram reply needed. It works once."""
        window = timedelta(minutes=self.settings.telegram_confirm_minutes)
        with session_scope() as session:
            latest = session.scalars(
                select(VoiceConfirmation)
                .where(VoiceConfirmation.chat_id == chat_id, VoiceConfirmation.user_id == user_id)
                .order_by(VoiceConfirmation.created_at.desc())
                .limit(1)
                .with_for_update()
            ).one_or_none()
            if (
                latest is None
                or latest.kind != ANSWER
                or latest.status != "redone"
                or latest.resolved_at is None
                or latest.resolved_at < datetime.now(UTC) - window
            ):
                return None
            question = session.get(Decision, latest.decision_id)
            if question is None or question.status is not DecisionStatus.PENDING:
                return None
            latest.status = "used"
            return str(latest.decision_id)

    def _open_question(self, decision_id: str) -> str | None:
        """Why a question can't take an answer any more, if it can't."""
        with session_scope() as session:
            decision = session.get(Decision, decision_id)
            if decision is None:
                return "Not done: that question is gone"
            if decision.status is not DecisionStatus.PENDING:
                return f"Not done: that question is already {decision.status}"
            return None

    def _request(self, body: str, user: dict[str, Any], input_kind: str, chat_id: int) -> str:
        """Post a request for the router, the same path as `ringkeeper ask`."""
        try:
            with session_scope() as session:
                message = threads.post_message(
                    session, body, author=_who(user), source=CHANNEL, input_kind=input_kind,
                    origin_chat_id=chat_id,
                )  # fmt: skip
                thread = str(message.thread_id)[:8]
        except RingkeeperError as error:
            return f"Not sent: {error}"
        return f"Got it. Routing it now (thread {thread})."

    def _hear(
        self, chat_id: int, user: dict[str, Any], note: VoiceNote, decision_id: str | None
    ) -> None:
        """Transcribe a voice note and ask the user to confirm what was heard.

        With a decision it's an answer to that question; without, a new request.
        """
        if decision_id and (refusal := self._open_question(decision_id)):
            self._say(chat_id, refusal)
            return
        try:
            stt = SttClient(self.settings)
            try:
                with session_scope() as session:
                    hint = vocabulary(session)
                transcript = transcribe_voice(self.api, stt, note, self.settings, hint)
            finally:
                stt.close()
        except VoiceError as error:
            self._say(chat_id, str(error))
            return
        expires = datetime.now(UTC) + timedelta(minutes=self.settings.telegram_confirm_minutes)
        with session_scope() as session:
            row = VoiceConfirmation(
                kind=ANSWER if decision_id else REQUEST, chat_id=chat_id, author=_who(user),
                user_id=int(user["id"]), transcript=transcript, expires_at=expires,
                decision_id=uuid.UUID(decision_id) if decision_id else None,
            )  # fmt: skip
            session.add(row)
            session.flush()
            ident = str(row.id)
        buttons = [
            {"text": "✅ Confirm", "callback_data": f"{CONFIRM_PREFIX}:{ident}:{CONFIRM}"},
            {"text": "🔁 Redo", "callback_data": f"{CONFIRM_PREFIX}:{ident}:{REDO}"},
        ]
        question = (
            f"Answer question {decision_id[:8]} with this?" if decision_id else "Send this request?"
        )
        text = heard(transcript, question)
        try:
            sent = self.api.send_message(chat_id, text, {"inline_keyboard": [buttons]})
        except TelegramError as error:
            logger.warning("could not send a voice confirmation: %s", error)
            return
        with session_scope() as session:
            session.get_one(VoiceConfirmation, ident).external_id = sent

    def _resolve(self, ident: str, action: str, chat_id: int, user: dict[str, Any]) -> str:
        """Confirm or Redo pressed. Each confirmation works once."""
        try:
            uuid.UUID(ident)
        except ValueError:
            return "This button is out of date"
        try:
            with session_scope() as session:
                row = session.get(VoiceConfirmation, ident, with_for_update=True)
                if row is None or row.chat_id != chat_id or action not in (CONFIRM, REDO):
                    return "This button is out of date"
                if row.user_id != user.get("id"):
                    return "Only the person who sent the voice note can confirm it"
                if row.status != "pending":
                    return f"Already {row.status}"
                transcript, external_id = row.transcript or "", row.external_id
                if row.expires_at <= datetime.now(UTC):
                    _close_confirmation(row, "expired")
                    outcome, feedback = EXPIRED, "This expired; send it again."
                elif action == REDO:
                    _close_confirmation(row, "redone")
                    outcome = (
                        "🔁 Redo: send a new voice note (or type the answer). "
                        "It will answer the same question."
                        if row.kind == ANSWER
                        else "🔁 Redo: send a new voice note."
                    )
                    feedback = "Send it again"
                elif row.kind == REQUEST:
                    message = threads.post_message(
                        session, transcript, author=row.author, source=CHANNEL,
                        input_kind="voice", origin_chat_id=row.chat_id,
                    )  # fmt: skip
                    _close_confirmation(row, "confirmed")
                    thread = str(message.thread_id)[:8]
                    outcome = f"✔ Sent. Routing it now (thread {thread})."
                    feedback = "Sent"
                else:
                    reply = inbox.Answer(text=transcript, voice=True)
                    answered = inbox.answer(
                        session, self.settings, str(row.decision_id), reply, by=_who(user),
                        via=inbox.Via.TELEGRAM, expected_kind=DecisionKind.QUESTION,
                    )  # fmt: skip
                    _close_confirmation(row, "confirmed")
                    outcome = "✔ Confirmed"
                    feedback = f"Done: {answered.decision.kind} {answered.decision.status}"
        except RingkeeperError as error:
            return f"Not done: {error}"
        text = EXPIRED if outcome == EXPIRED else f"{heard(transcript)}\n{outcome}"
        self._edit(chat_id, external_id, text)
        self.close_finished()
        return feedback

    def expire_confirmations(self) -> int:
        """Close confirmations nobody answered in time; their transcripts are cleared."""
        with session_scope() as session:
            rows = session.scalars(
                select(VoiceConfirmation)
                .where(
                    VoiceConfirmation.status == "pending",
                    VoiceConfirmation.expires_at <= datetime.now(UTC),
                )
                .with_for_update(skip_locked=True)
            ).all()
            done = [(row.chat_id, row.external_id) for row in rows]
            for row in rows:
                _close_confirmation(row, "expired")
        for chat_id, external_id in done:
            self._edit(chat_id, external_id, EXPIRED)
        return len(done)

    def _edit(self, chat_id: int, message_id: int | None, text: str) -> None:
        if message_id is None:
            return
        try:
            self.api.edit_message(chat_id, message_id, scrub(text))
        except TelegramError as error:
            logger.warning("could not update Telegram message: %s", error)

    def _say(self, chat_id: int, text: str) -> None:
        try:
            self.api.send_message(chat_id, scrub(text))
        except TelegramError as error:
            logger.warning("could not reply on Telegram: %s", error)

    # --- Polling ---

    def _offset(self) -> int | None:
        with session_scope() as session:
            row = session.get(Setting, OFFSET_KEY)
            return int(row.value["offset"]) if row else None

    def _save_offset(self, offset: int) -> None:
        with session_scope() as session:
            statement = insert(session, Setting).values(key=OFFSET_KEY, value={"offset": offset})
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=["key"], set_={"value": statement.excluded.value}
                )
            )

    def poll_once(self, timeout: int | None = None) -> int:
        """One getUpdates long poll; handle what came back. Returns how many updates."""
        wait = self.settings.telegram_poll_seconds if timeout is None else timeout
        updates = self.api.get_updates(self._offset(), wait)
        for update in updates:
            try:
                self.handle(update)
            except Exception:
                logger.exception("handling a Telegram update failed")
            # Saved per update, so a crash never replays an answer.
            self._save_offset(int(update["update_id"]) + 1)
        return len(updates)

    def run_forever(self, stop: threading.Event) -> None:
        wake = threading.Event()
        start_listener(
            self.settings, [DECISIONS_CHANNEL], lambda _c, _p: wake.set(), stop, "rk-tg-listen"
        )
        threading.Thread(
            target=self._poll_forever, args=(stop,), daemon=True, name="rk-tg-poll"
        ).start()
        while not stop.is_set():
            try:
                self.send_new()
                self.close_finished()
                self.expire_confirmations()
                self.send_notices()
            except Exception:
                logger.exception("Telegram notification pass failed")
            wake.wait(5.0)
            wake.clear()

    def _poll_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.poll_once()
            except TelegramError as error:
                logger.warning("%s", error)
                stop.wait(5.0)
            except Exception:
                logger.exception("Telegram poll failed")
                stop.wait(5.0)
