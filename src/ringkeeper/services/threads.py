"""Threads and messages: what the user said, and what Ringkeeper said back.

A user message is stored unrouted and the router (in the api service) is
woken with NOTIFY. Routing writes the router's output onto the message, plus
a short reply message in the same thread.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, cast, select, text
from sqlalchemy.orm import Session

from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import Message, MessageRole, Thread
from ringkeeper.services.decisions import MIN_ID_PREFIX

MESSAGES_CHANNEL = "ringkeeper_messages"
SOURCES = frozenset({"cli", "api", "web", "telegram"})
INPUTS = frozenset({"text", "voice"})  # voice: a transcript, confirmed by the user
TITLE_CHARS = 80
MAX_MESSAGE_CHARS = 20_000


def notify_router(session: Session) -> None:
    session.execute(text(f"NOTIFY {MESSAGES_CHANNEL}"))


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
        raise NotFoundError(f"thread '{ident}' not found")
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
) -> Message:
    """Store a user message (in a new thread unless one is given) for the router."""
    body = body.strip()
    if not body:
        raise RuleViolationError("the message is empty")
    if len(body) > MAX_MESSAGE_CHARS:
        raise RuleViolationError(f"the message is longer than {MAX_MESSAGE_CHARS} characters")
    if source not in SOURCES:
        raise RuleViolationError(f"unknown message source '{source}'")
    if input_kind not in INPUTS:
        raise RuleViolationError(f"unknown message input '{input_kind}'")
    if thread is None:
        title = body.splitlines()[0][:TITLE_CHARS]
        target = Thread(title=title, source=source)
        session.add(target)
        session.flush()
    else:
        target = get_thread(session, thread)
        target.updated_at = datetime.now(UTC)
    message = Message(
        thread_id=target.id, role=MessageRole.USER, author=author, source=source, body=body,
        input=input_kind,
    )  # fmt: skip
    session.add(message)
    session.flush()
    notify_router(session)
    return message


def add_reply(
    session: Session, thread_id: uuid.UUID, body: str, *, role: MessageRole = MessageRole.ROUTER
) -> Message:
    message = Message(
        thread_id=thread_id, role=role, author="ringkeeper", source="system", body=body
    )
    session.add(message)
    session.flush()
    return message


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


def thread_messages(session: Session, thread_id: uuid.UUID) -> Sequence[Message]:
    return session.scalars(
        select(Message).where(Message.thread_id == thread_id).order_by(Message.created_at)
    ).all()


def claim_unrouted(session: Session) -> Message | None:
    """The oldest user message nobody has routed yet, locked against other routers."""
    return session.scalars(
        select(Message)
        .where(
            Message.role == MessageRole.USER,
            Message.routed_at.is_(None),
            Message.route_error.is_(None),
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
