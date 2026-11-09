"""Threads and messages: what the user said, and what Ringkeeper said back.

A user message is stored unrouted and the router (in the api service) is
woken with NOTIFY. Routing writes the router's output onto the message, plus
a short reply message in the same thread.

A request sent for later (`run_at`) waits: the router leaves it until then, and it can be
canceled before. One that comes due while Ringkeeper isn't running runs when it starts
again, unless it's more than LATE past its time: then it's dropped, and the thread says so.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from ringkeeper import sensitive
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import Message, MessageRole, Setting, Task, TaskStatus, Thread
from ringkeeper.services import titles
from ringkeeper.services.decisions import MIN_ID_PREFIX
from ringkeeper.storage import events

MESSAGES_CHANNEL = "ringkeeper_messages"
SOURCES = frozenset({"cli", "api", "web", "telegram", "pass", "slack", "discord"})
INPUTS = frozenset({"text", "voice"})  # voice: a transcript, confirmed by the user
TITLE_CHARS = 80
MAX_MESSAGE_CHARS = 20_000
SOONEST = timedelta(minutes=1)  # sooner than this: just send it
FURTHEST = timedelta(days=90)
LATE = timedelta(hours=6)  # due longer ago than this when the router gets to it: dropped
CANCELED = "Canceled. It won't run."


def notify_router(session: Session) -> None:
    events.notify(session, MESSAGES_CHANNEL)


def normalize(body: str) -> str:
    """The key routing examples match on: lowercased, whitespace collapsed."""
    return " ".join(body.lower().split())


def get_thread(session: Session, ident: str) -> Thread:
    ident = ident.strip().lower()
    query = select(Thread)
    try:
        query = query.where(Thread.id == uuid.UUID(ident))
    except ValueError:
        if len(ident) < MIN_ID_PREFIX:
            raise NotFoundError(
                f"thread '{ident}' not found; use the full id or 8+ characters"
            ) from None
        query = query.where(cast(Thread.id, String).startswith(ident))
    matches = session.scalars(query.limit(2)).all()
    if not matches:
        raise NotFoundError("That conversation isn't here any more. Go back to Home.")
    if len(matches) > 1:
        raise NotFoundError(f"thread prefix '{ident}' is ambiguous; use more characters")
    return matches[0]


def list_threads(session: Session, *, limit: int = 20) -> Sequence[Thread]:
    return session.scalars(select(Thread).order_by(Thread.updated_at.desc()).limit(limit)).all()


def post_message(
    session: Session,
    body: str,
    *,
    author: str,
    source: str,
    thread: str | None = None,
    input_kind: str = "text",
    origin_chat_id: int | None = None,
    attachments: list[str] | None = None,
    run_at: datetime | None = None,
    target_client: str | None = None,
    target_project: str | None = None,
) -> Message:
    """Store a user message (in a new thread unless one is given) for the router.

    `origin_chat_id`: the Telegram chat it came from; its news goes only there. `run_at`:
    sent for later, the router leaves it until then."""
    body = body.strip()
    if not body:
        raise RuleViolationError("Type a message first.")
    if len(body) > MAX_MESSAGE_CHARS:
        raise RuleViolationError(
            f"That message is too long. Keep it under {MAX_MESSAGE_CHARS:,} characters."
        )
    if source not in SOURCES:
        raise RuleViolationError(f"unknown message source '{source}'")
    if input_kind not in INPUTS:
        raise RuleViolationError(f"unknown message input '{input_kind}'")
    if run_at is not None:
        run_at = _when(run_at)
    body = sensitive.mask(body)
    if thread is None:
        title = body.splitlines()[0][:TITLE_CHARS]
        target = Thread(title=title, source=source, origin_chat_id=origin_chat_id)
        session.add(target)
        session.flush()
    else:
        target = get_thread(session, thread)
        target.updated_at = datetime.now(UTC)
    message = Message(
        thread_id=target.id, role=MessageRole.USER, author=author, source=source, body=body,
        input=input_kind, attachments=attachments or [], run_at=run_at,
        target_client=target_client, target_project=target_project,
    )  # fmt: skip
    session.add(message)
    session.flush()
    notify_router(session)
    return message


QUIET = timedelta(hours=2)  # a chat's next message after this starts a new conversation
NEW_WORDS = frozenset({"new", "/new", "new conversation", "new chat", "start over"})
STARTED_NEW = "OK. Your next message starts a new conversation."


def starts_new(text: str) -> tuple[bool, str]:
    """Whether a chat message starts a new conversation ("new", or "new: <request>"), and
    the request in it, if any."""
    said = text.strip()
    if said.lower().rstrip(".!") in NEW_WORDS:
        return True, ""
    if said.lower().startswith("new:"):
        return True, said[4:].strip()
    return False, said


def _fresh_key(source: str, who: str) -> str:
    return f"chat-new:{source}:{who}"


def start_fresh(session: Session, source: str, who: str) -> None:
    """The next message from this chat starts a new conversation."""
    key = _fresh_key(source, who)
    row = session.get(Setting, key)
    stamp = {"at": datetime.now(UTC).isoformat()}
    if row is None:
        session.add(Setting(key=key, value=stamp))
    else:
        row.value = stamp
    session.flush()


def chat_thread(
    session: Session,
    source: str,
    who: str,
    *,
    chat_id: int | None = None,
    now: datetime | None = None,
) -> Thread | None:
    """The conversation a chat's next message continues: its last one, unless it's been
    quiet for 2 hours, was archived, or the person said "new" since. `who` is the start of
    the author (Slack and Discord: "slack:U123"); Telegram goes by its chat."""
    now = now or datetime.now(UTC)
    query = (
        select(Message)
        .join(Thread, Thread.id == Message.thread_id)
        .where(Message.source == source, Message.role == MessageRole.USER)
    )
    if chat_id is not None:
        query = query.where(Thread.origin_chat_id == chat_id)
    else:
        query = query.where(Message.author.startswith(who, autoescape=True))
    last = session.scalars(query.order_by(Message.created_at.desc()).limit(1)).first()
    if last is None:
        return None
    thread = session.get_one(Thread, last.thread_id)
    latest = session.scalar(
        select(func.max(Message.created_at)).where(Message.thread_id == thread.id)
    )
    fresh = session.get(Setting, _fresh_key(source, who))
    if fresh is not None and datetime.fromisoformat(str(fresh.value["at"])) > last.created_at:
        return None
    if thread.archived_at is not None or latest is None or latest < now - QUIET:
        return None
    return thread


def _when(run_at: datetime) -> datetime:
    if run_at.tzinfo is None:
        raise RuleViolationError("Give the time with its time zone.")
    run_at = run_at.astimezone(UTC)
    now = datetime.now(UTC)
    if run_at < now + SOONEST:
        raise RuleViolationError("Choose a time at least a minute from now, or just send it.")
    if run_at > now + FURTHEST:
        raise RuleViolationError("Choose a time in the next 90 days.")
    return run_at


def waiting(message: Message, now: datetime | None = None) -> bool:
    """Sent for later and not yet due, nor canceled, nor routed."""
    return (
        message.run_at is not None
        and message.routed_at is None
        and message.canceled_at is None
        and message.run_at > (now or datetime.now(UTC))
    )


def scheduled(session: Session, *, limit: int = 50) -> Sequence[Message]:
    """Requests sent for later that haven't run, soonest first."""
    return session.scalars(
        select(Message)
        .where(
            Message.role == MessageRole.USER,
            Message.run_at.is_not(None),
            Message.routed_at.is_(None),
            Message.canceled_at.is_(None),
        )
        .order_by(Message.run_at)
        .limit(limit)
    ).all()


def cancel(session: Session, message_id: uuid.UUID) -> Message:
    """Stops a request sent for later before it runs."""
    message = get_message(session, message_id, lock=True)
    if message.role is not MessageRole.USER or message.run_at is None:
        raise RuleViolationError("Only a request sent for later can be canceled.")
    if message.canceled_at is not None:
        raise RuleViolationError("That request was already canceled.")
    if message.routed_at is not None:
        raise RuleViolationError("That request already ran, so it can't be canceled.")
    message.canceled_at = datetime.now(UTC)
    add_reply(session, message.thread_id, CANCELED, role=MessageRole.SYSTEM)
    return message


def drop_if_late(session: Session, message: Message, now: datetime | None = None) -> bool:
    """A request sent for later that's long past its time (Ringkeeper wasn't running then)
    doesn't run: true when it was dropped."""
    now = now or datetime.now(UTC)
    if message.run_at is None or now - message.run_at <= LATE:
        return False
    message.canceled_at = now
    add_reply(
        session, message.thread_id,
        "This was due earlier, but Ringkeeper wasn't running then, so it didn't run. Send it "
        "again if you still want it.",
        role=MessageRole.SYSTEM,
    )  # fmt: skip
    return True


def add_reply(
    session: Session,
    thread_id: uuid.UUID,
    body: str,
    *,
    role: MessageRole = MessageRole.ROUTER,
    routing: dict[str, Any] | None = None,
) -> Message:
    message = Message(
        thread_id=thread_id, role=role, author="ringkeeper", source="system",
        body=sensitive.mask(body),
        routing=routing,
    )  # fmt: skip
    session.add(message)
    session.flush()
    return message


ASKS_PROJECT = "Which project is this for?"


def working_on(session: Session, task: Task) -> str:
    """What a started task is, in the words the thread shows."""
    what = task.outcome or "the task"
    if task.status is TaskStatus.NEEDS_INPUT:  # over the client's monthly cap: it asks first
        return f"Waiting for your OK before starting in {titles.of(session, task.project)}: {what}"
    plan = " I'll show you a plan first." if task.phase is not None else ""
    return f"Working on it in {titles.of(session, task.project)}: {what}{plan}"


def get_message(session: Session, message_id: uuid.UUID, *, lock: bool = False) -> Message:
    query = select(Message).where(Message.id == message_id)
    if lock:
        query = query.with_for_update()
    message = session.scalars(query).one_or_none()
    if message is None:
        raise NotFoundError(f"message '{message_id}' not found")
    return message


def recent_messages(
    session: Session, thread_id: uuid.UUID, *, limit: int, before: datetime | None = None
) -> list[Message]:
    """The last `limit` messages of a thread before a point in time, oldest first."""
    query = select(Message).where(Message.thread_id == thread_id)
    if before is not None:
        query = query.where(Message.created_at < before)
    rows = session.scalars(query.order_by(Message.created_at.desc()).limit(limit)).all()
    return list(reversed(rows))


def thread_tasks(session: Session, thread_id: uuid.UUID, limit: int = 10) -> list[Task]:
    """The conversation's latest tasks, oldest first."""
    query = select(Task).where(Task.thread_id == thread_id)
    rows = session.scalars(query.order_by(Task.created_at.desc()).limit(limit)).all()
    return list(reversed(rows))


def thread_messages(session: Session, thread_id: uuid.UUID) -> Sequence[Message]:
    return session.scalars(
        select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at)
    ).all()


def claim_unrouted(session: Session) -> Message | None:
    """The oldest user message nobody has routed yet, locked against other routers. One sent
    for later waits until its time; a canceled one never comes."""
    now = datetime.now(UTC)
    return session.scalars(
        select(Message)
        .where(
            Message.role == MessageRole.USER,
            Message.routed_at.is_(None),
            Message.route_error.is_(None),
            Message.canceled_at.is_(None),
            or_(Message.run_at.is_(None), Message.run_at <= now),
        )
        .order_by(Message.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()


def mark_routed(
    session: Session, message: Message, routing: dict[str, Any] | None, error: str | None = None
) -> None:
    message.routing = routing
    message.route_error = error
    message.routed_at = datetime.now(UTC)
    session.flush()
