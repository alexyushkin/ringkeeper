"""The Telegram bridge: send each new decision, take answers from buttons and replies.

Telegram bot chats aren't end-to-end encrypted, so this is a notification
channel only. A notification carries the kind, project, instance and a short
scrubbed one-line description (`telegram_summary_chars`), plus buttons. Never
SQL, arguments, diffs or plan text: full details stay in the CLI and API.

Only chats in `telegram_chat_ids` get notifications, and updates from any
other chat are ignored. Every answer is audited as via=telegram with the
Telegram user who pressed the button or replied.
"""

import logging
import threading
import uuid
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.listen import start_listener
from ringkeeper.models import Decision, DecisionKind, DecisionStatus, Notification, Setting
from ringkeeper.scrub import scrub
from ringkeeper.services import inbox
from ringkeeper.services.decisions import DECISIONS_CHANNEL
from ringkeeper.telegram.api import BotApi, TelegramError

logger = logging.getLogger(__name__)

CHANNEL = "telegram"
OFFSET_KEY = "telegram.offset"
CALLBACK_PREFIX = "rk"
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
NOT_A_REPLY = (
    "Ringkeeper only takes answers here: press a button, or reply to a question. "
    "Send new requests with `ringkeeper ask`."
)


def callback_data(decision: Decision, index: int) -> str:
    """At most 64 bytes, as Telegram requires: 'rk:<uuid>:<option index>'."""
    return f"{CALLBACK_PREFIX}:{decision.id}:{index}"


def render(decision: Decision, limit: int) -> str:
    head = [KIND_LABELS[decision.kind]]
    if decision.project:
        head.append(decision.project)
    if decision.instance:
        head.append(decision.instance)
    lines = [" · ".join(head), inbox.summary(decision, limit), f"id {str(decision.id)[:8]}"]
    if decision.kind is DecisionKind.QUESTION:
        lines.append("Reply to this message with your answer.")
    return scrub("\n".join(lines))


def keyboard(decision: Decision) -> dict[str, Any]:
    if decision.kind is DecisionKind.QUESTION:
        return {"force_reply": True, "input_field_placeholder": "Your answer"}
    buttons = [
        {"text": BUTTON_LABELS.get(option, option), "callback_data": callback_data(decision, i)}
        for i, option in enumerate(inbox.options(decision))
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
        sent = select(Notification.decision_id).where(Notification.channel == CHANNEL)
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
                text = render(decision, self.settings.telegram_summary_chars)
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
                text = f"{render(decision, self.settings.telegram_summary_chars)}\n"
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
        feedback = "Unknown button"
        if len(parts) == 3 and parts[0] == CALLBACK_PREFIX:
            feedback = self._press(parts[1], parts[2], cast(dict[str, Any], callback["from"]))
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
        if not replied and text.split("@")[0].split(" ")[0] == "/start":
            self._say(chat_id, WELCOME)
            return
        decision_id = self._question_for(chat_id, replied) if replied else None
        if decision_id is None or not text:
            self._say(chat_id, NOT_A_REPLY)
            return
        user = cast(dict[str, Any], message.get("from") or {})
        self._say(chat_id, self._answer(decision_id, inbox.Answer(text=text), user))
        self.close_finished()

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
            statement = insert(Setting).values(key=OFFSET_KEY, value={"offset": offset})
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
