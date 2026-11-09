"""What each channel sends, once, and the loop that sends it.

Every channel (Telegram, each Slack and Discord one) is a key in `notifications`
("telegram", "slack:<id>", "discord:<id>"), so each thing goes to it once. What
its prefs say not to send is recorded as skipped and never looked at again;
what comes up in quiet hours waits for them to end. Only what happened after
the channel was set up (`since`) and in the last day counts.

A Slack or Discord channel set to take requests (config "requests") takes a DM from its
approver as a new request, the same path as one typed in the app; anyone else is told
no. Its news (what the router did, each task's end) goes back to that DM.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ringkeeper.channels import cards, links, news
from ringkeeper.channels.prefs import APPROVALS, FAILURES, FINISHED, ROUTINES, Prefs
from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.listen import start_listener
from ringkeeper.models import (
    TERMINAL_STATUSES,
    Channel,
    Decision,
    DecisionStatus,
    Message,
    MessageRole,
    Notification,
    Project,
    Routine,
    RoutineRun,
    Task,
    TaskStatus,
    Thread,
)
from ringkeeper.scrub import scrub
from ringkeeper.services import inbox, threads
from ringkeeper.services.decisions import DECISIONS_CHANNEL

logger = logging.getLogger(__name__)

SENT, RUNNING, CLOSED, FAILED, SKIPPED = "sent", "running", "closed", "failed", "skipped"
LOOKBACK = timedelta(days=1)
NEWS_CHARS = 600
NOT_YOURS = "Not allowed: only the person set up for this channel can send requests here."
REQUESTS_OFF = (
    "New requests by DM are off. Turn them on in Ringkeeper (Settings > Notifications, this "
    "channel), or use the app."
)
TEXT_ONLY = "Send a request as a text message."


class ChannelError(RingkeeperError):
    """Slack or Discord said no, or couldn't be reached."""


def task_event(task: Task, routine: Routine | None) -> str | None:
    """Which event a task's end is: a routine's run (when it sends a summary), a
    failure, or a finished task. Stopped by you: none."""
    if routine is not None and routine.notify == "channels":
        return ROUTINES
    if task.status in cards.FAILED:
        return FAILURES
    if routine is None and task.status is TaskStatus.SUCCEEDED:
        return FINISHED
    return None


@dataclass
class Due:
    decisions: list[Decision]
    tasks: list[tuple[Task, Routine | None]]
    # Of `decisions`: those from a task asked for in this chat, sent back to that chat.
    mine: set[uuid.UUID] = field(default_factory=set[uuid.UUID])


def asked_here(session: Session, decision: Decision, source: str, asker: str | None) -> bool:
    """Whether a decision comes from a request sent from this chat: its conversation
    started there (Slack and Discord: from this person's DM)."""
    thread_id = decision.thread_id
    if thread_id is None and decision.task_id is not None:
        task = session.get(Task, decision.task_id)
        thread_id = task.thread_id if task is not None else None
    if thread_id is None:
        return False
    thread = session.get(Thread, thread_id)
    if thread is None or thread.source != source:
        return False
    if asker is None:
        return True
    first = session.scalar(
        select(Message.author)
        .where(Message.thread_id == thread_id, Message.role == MessageRole.USER)
        .order_by(Message.created_at)
        .limit(1)
    )
    return bool(first and first.startswith(asker))


def _clients(session: Session) -> dict[str, str]:
    rows = session.execute(select(Project.name, Project.owner)).all()
    return {str(row[0]): str(row[1]) for row in rows}


def _skip(session: Session, key: str, **subject: uuid.UUID) -> None:
    session.add(Notification(channel=key, chat_id=0, status=SKIPPED, **subject))


def due(
    session: Session,
    key: str,
    prefs: Prefs,
    since: datetime,
    now: datetime,
    own_threads: str | None = None,
    asker: str | None = None,
) -> Due:
    """What this channel should send now. What it won't send is recorded as skipped.

    `own_threads`: tasks asked for from this channel are left to its own notices about
    them, and what they wait on (an approval, a question) is always sent, to that chat
    (`asker`: only those asked by this person, for a Slack or Discord DM)."""
    if prefs.quiet(now):
        return Due([], [])
    inbox.list_pending(session)  # expires stale ones first
    owners = _clients(session)
    # Sent already, unless since snoozed: a snoozed one coming back is sent again.
    noted = (
        select(Notification.decision_id)
        .join(Decision, Decision.id == Notification.decision_id)
        .where(
            Notification.channel == key,
            or_(
                Decision.snoozed_until.is_(None), Notification.created_at >= Decision.snoozed_until
            ),
        )
    )
    decisions: list[Decision] = []
    mine: set[uuid.UUID] = set()
    found = session.scalars(
        select(Decision)
        .where(
            Decision.status == DecisionStatus.PENDING,
            Decision.id.not_in(noted),
            inbox.awake(now),
        )
        .order_by(Decision.created_at)
    ).all()
    for decision in found:
        if decision.snoozed_until is not None:  # back from a snooze: what was sent before goes
            session.execute(
                delete(Notification).where(
                    Notification.channel == key, Notification.decision_id == decision.id
                )
            )
        if own_threads is not None and asked_here(session, decision, own_threads, asker):
            decisions.append(decision)
            mine.add(decision.id)
        elif APPROVALS in prefs.events and prefs.covers(
            decision.project, owners.get(decision.project or "")
        ):
            decisions.append(decision)
        else:
            _skip(session, key, decision_id=decision.id)
    ended = select(Notification.task_id).where(
        Notification.channel == key, Notification.task_id.is_not(None)
    )
    query = (
        select(Task, Routine)
        .outerjoin(RoutineRun, RoutineRun.task_id == Task.id)
        .outerjoin(Routine, Routine.id == RoutineRun.routine_id)
        .where(
            Task.status.in_(list(TERMINAL_STATUSES)),
            Task.finished_at >= max(since, now - LOOKBACK),
            Task.id.not_in(ended),
        )
        .order_by(Task.finished_at)
    )
    if own_threads is not None:
        query = query.outerjoin(Thread, Thread.id == Task.thread_id).where(
            or_(Thread.id.is_(None), Thread.source.is_(None), Thread.source != own_threads)
        )
    rows = session.execute(query).all()
    tasks: list[tuple[Task, Routine | None]] = []
    for task, routine in rows:
        event = task_event(task, routine)
        if event in prefs.events and prefs.covers(task.project, owners.get(task.project)):
            tasks.append((task, routine))
        else:
            _skip(session, key, task_id=task.id)
    session.flush()
    return Due(decisions, tasks, mine)


class Transport(Protocol):
    """Slack's or Discord's side of a channel (channels.slack, channels.discord)."""

    def post(self, card: cards.Card) -> str:
        """Sends a message; returns its reference ("<channel id>/<message id>")."""
        ...

    def update(self, ref: str, card: cards.Card) -> None: ...

    def direct(self, user: str, card: cards.Card) -> str:
        """A message in the DM with this person; returns its reference."""
        ...

    def listen(
        self, stop: threading.Event, on_press: "OnPress", on_message: "OnMessage | None" = None
    ) -> None:
        """Takes button presses (and DMs, with on_message) until `stop`; each is answered
        with what on_press or on_message says."""
        ...

    def close(self) -> None: ...


# (action, user id, who for the audit log) -> what to tell the person who pressed it
OnPress = Callable[[str, str, str], str]
# (user id, text, who for the audit log) -> what to say back in the DM, if anything
OnMessage = Callable[[str, str, str], str | None]


class Runner:
    """One Slack or Discord channel: sends what's due, updates what's decided, and
    answers its buttons."""

    def __init__(self, channel_id: uuid.UUID, settings: Settings, transport: Transport) -> None:
        self.channel_id = channel_id
        self.settings = settings
        self.transport = transport
        with session_scope(commit=False) as session:
            channel = session.get_one(Channel, channel_id)
            self.kind = channel.kind
            self.key = f"{channel.kind}:{channel.id}"
            self.prefs = Prefs.load(channel.prefs)
            self.since = channel.since
            self.approver = str(channel.config.get("approver") or "")
            self.requests = bool(channel.config.get("requests"))

    @property
    def asker(self) -> str:
        """How a request by DM from the approver is signed (the start of its author)."""
        return f"{self.kind}:{self.approver}"

    def _link(self, session: Session, path: str) -> str | None:
        return links.open_url(session, self.settings, path)

    def send_due(self, now: datetime | None = None) -> int:
        sent = 0
        with session_scope() as session:
            found = due(
                session, self.key, self.prefs, self.since, now or datetime.now(UTC),
                own_threads=self.kind if self.requests else None, asker=self.asker,
            )  # fmt: skip
            for decision in found.decisions:
                link = self._link(session, links.decision_page(self.settings, decision))
                card = cards.decision_card(decision, link)
                here = decision.id in found.mine  # asked for in the DM: answered there
                sent += self._send(session, card, SENT, direct=here, decision_id=decision.id)
            for task, routine in found.tasks:
                if routine is not None and task_event(task, routine) == ROUTINES:
                    link = self._link(session, links.routine_page(routine.id))
                    card = cards.routine_card(routine, task, link)
                else:
                    link = self._link(session, links.task_page(self.settings, task.id))
                    card = cards.task_card(task, link)
                sent += self._send(session, card, CLOSED, task_id=task.id)
        return sent

    def _send(
        self,
        session: Session,
        card: cards.Card,
        status: str,
        *,
        direct: bool = False,
        **subject: uuid.UUID,
    ) -> int:
        note = Notification(channel=self.key, chat_id=0, **subject)
        try:
            ref = (
                self.transport.direct(self.approver, card) if direct else self.transport.post(card)
            )
            note.external_ref, note.status = ref, status
        except ChannelError as error:
            note.status, note.error = FAILED, str(error)[:500]
            logger.warning("could not send to %s: %s", self.kind, error)
        session.add(note)
        session.flush()
        return int(note.status != FAILED)

    def close_finished(self) -> int:
        """Updates sent messages whose decision has moved on: the outcome, and no
        buttons but Open."""
        closed = 0
        with session_scope() as session:
            rows = session.execute(
                select(Notification, Decision)
                .join(Decision, Decision.id == Notification.decision_id)
                .where(Notification.channel == self.key, Notification.status.in_([SENT, RUNNING]))
            ).all()
            for note, decision in rows:
                running = decision.status is DecisionStatus.PENDING and bool(decision.answer)
                if decision.status is DecisionStatus.PENDING and not running:
                    continue
                if note.external_ref is None or (running and note.status == RUNNING):
                    continue
                link = self._link(session, links.decision_page(self.settings, decision))
                try:
                    self.transport.update(note.external_ref, cards.closed_card(decision, link))
                except ChannelError as error:
                    logger.warning("could not update a %s message: %s", self.kind, error)
                if running:
                    note.status = RUNNING
                else:
                    note.status = CLOSED
                    closed += 1
        return closed

    # --- Requests by DM ---

    def on_message(self, user: str, text: str, who: str) -> str | None:
        """A DM: from the approver, with requests on, a new request."""
        if not self.approver or user != self.approver:
            return NOT_YOURS
        if not self.requests:
            return REQUESTS_OFF
        if not text.strip():
            return TEXT_ONLY
        fresh, text = threads.starts_new(text)
        try:
            with session_scope() as session:
                if fresh:
                    threads.start_fresh(session, self.kind, self.asker)
                    if not text:
                        return threads.STARTED_NEW
                place = threads.chat_thread(session, self.kind, self.asker)
                message = threads.post_message(
                    session, text, author=who, source=self.kind,
                    thread=str(place.id) if place is not None else None,
                )  # fmt: skip
                link = (
                    None
                    if place
                    else self._link(session, links.thread_page(self.settings, message.thread_id))
                )
        except RingkeeperError as error:
            return f"Not sent: {error}"
        return news.got_it(text, link)

    def send_news(self) -> int:
        """For requests by DM: what the router did, and each task's end, back in the DM
        (whatever the prefs say: these are answers to what was asked)."""
        if not self.requests or not self.approver:
            return 0
        sent = 0
        with session_scope() as session:
            told = select(Notification.message_id).where(
                Notification.channel == self.key, Notification.message_id.is_not(None)
            )
            mine = (
                Message.source == self.kind,
                Message.role == MessageRole.USER,
                Message.author.startswith(self.asker, autoescape=True),
                Message.created_at >= self.since,
            )
            for message in session.scalars(
                select(Message)
                .where(*mine, Message.routed_at.is_not(None), Message.id.not_in(told))
                .order_by(Message.created_at)
            ).all():
                text = news.routed_text(session, message, NEWS_CHARS)
                sent += self._send_direct(session, text, message_id=message.id)
            ended = select(Notification.task_id).where(
                Notification.channel == self.key, Notification.task_id.is_not(None)
            )
            for task in session.scalars(
                select(Task)
                .join(Message, Message.id == Task.message_id)
                .where(
                    *mine,
                    Task.status.in_(list(TERMINAL_STATUSES)),
                    Task.id.not_in(ended),
                )
                .order_by(Task.finished_at)
            ).all():
                link = self._link(session, links.task_page(self.settings, task.id))
                sent += self._send_direct(session, news.result_text(task, link), task_id=task.id)
        return sent

    def _send_direct(self, session: Session, text: str, **subject: uuid.UUID) -> int:
        note = Notification(channel=self.key, chat_id=0, **subject)
        try:
            note.external_ref = self.transport.direct(self.approver, cards.Card(scrub(text)))
            note.status = CLOSED  # nothing to update later
        except ChannelError as error:
            note.status, note.error = FAILED, str(error)[:500]
            logger.warning("could not send to a %s DM: %s", self.kind, error)
        session.add(note)
        session.flush()
        return int(note.status != FAILED)

    def on_press(self, action: str, user: str, who: str) -> str:
        reply = cards.press(
            self.settings, action, user=user, approver=self.approver, who=who,
            via=inbox.Via(self.kind),
        )  # fmt: skip
        self.close_finished()
        return reply

    def run_forever(self, stop: threading.Event) -> None:
        wake = threading.Event()
        start_listener(
            self.settings, [DECISIONS_CHANNEL], lambda _c, _p: wake.set(), stop,
            f"rk-{self.kind}-listen",
        )  # fmt: skip
        threading.Thread(
            target=self._listen_forever, args=(stop,), daemon=True, name=f"rk-{self.kind}-in"
        ).start()
        try:
            while not stop.is_set():
                try:
                    self.send_due()
                    self.close_finished()
                    self.send_news()
                except Exception:
                    logger.exception("%s notification pass failed", self.kind)
                wake.wait(5.0)
                wake.clear()
        finally:
            self.transport.close()

    def _listen_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.transport.listen(stop, self.on_press, self.on_message)
            except Exception as error:
                logger.warning("%s connection dropped: %s", self.kind, error)
            stop.wait(5.0)
