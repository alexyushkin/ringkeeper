"""What each channel sends, once, and the loop that sends it.

Every channel (Telegram, each Slack and Discord one) is a key in `notifications`
("telegram", "slack:<id>", "discord:<id>"), so each thing goes to it once. What
its prefs say not to send is recorded as skipped and never looked at again;
what comes up in quiet hours waits for them to end. Only what happened after
the channel was set up (`since`) and in the last day counts.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ringkeeper.channels import cards, links
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
    Notification,
    Project,
    Routine,
    RoutineRun,
    Task,
    TaskStatus,
    Thread,
)
from ringkeeper.services import inbox
from ringkeeper.services.decisions import DECISIONS_CHANNEL

logger = logging.getLogger(__name__)

SENT, RUNNING, CLOSED, FAILED, SKIPPED = "sent", "running", "closed", "failed", "skipped"
LOOKBACK = timedelta(days=1)


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
) -> Due:
    """What this channel should send now. What it won't send is recorded as skipped.

    `own_threads`: tasks asked for from this channel (Telegram) are left to its
    own notices about them."""
    if prefs.quiet(now):
        return Due([], [])
    inbox.list_pending(session)  # expires stale ones first
    owners = _clients(session)
    noted = select(Notification.decision_id).where(
        Notification.channel == key, Notification.decision_id.is_not(None)
    )
    decisions: list[Decision] = []
    for decision in session.scalars(
        select(Decision)
        .where(Decision.status == DecisionStatus.PENDING, Decision.id.not_in(noted))
        .order_by(Decision.created_at)
    ):
        if APPROVALS in prefs.events and prefs.covers(
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
    return Due(decisions, tasks)


class Transport(Protocol):
    """Slack's or Discord's side of a channel (channels.slack, channels.discord)."""

    def post(self, card: cards.Card) -> str:
        """Sends a message; returns its reference ("<channel id>/<message id>")."""
        ...

    def update(self, ref: str, card: cards.Card) -> None: ...

    def listen(self, stop: threading.Event, on_press: "OnPress") -> None:
        """Takes button presses until `stop`; each is answered with what on_press says."""
        ...

    def close(self) -> None: ...


# (action, user id, who for the audit log) -> what to tell the person who pressed it
OnPress = Callable[[str, str, str], str]


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

    def _link(self, session: Session, path: str) -> str | None:
        return links.open_url(session, self.settings, path)

    def send_due(self, now: datetime | None = None) -> int:
        sent = 0
        with session_scope() as session:
            found = due(session, self.key, self.prefs, self.since, now or datetime.now(UTC))
            for decision in found.decisions:
                link = self._link(session, links.decision_page(self.settings, decision))
                sent += self._send(
                    session, cards.decision_card(decision, link), SENT, decision_id=decision.id
                )
            for task, routine in found.tasks:
                if routine is not None and task_event(task, routine) == ROUTINES:
                    link = self._link(session, links.routine_page(routine.id))
                    card = cards.routine_card(routine, task, link)
                else:
                    link = self._link(session, links.task_page(self.settings, task.id))
                    card = cards.task_card(task, link)
                sent += self._send(session, card, CLOSED, task_id=task.id)
        return sent

    def _send(self, session: Session, card: cards.Card, status: str, **subject: uuid.UUID) -> int:
        note = Notification(channel=self.key, chat_id=0, **subject)
        try:
            note.external_ref, note.status = self.transport.post(card), status
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
                except Exception:
                    logger.exception("%s notification pass failed", self.kind)
                wake.wait(5.0)
                wake.clear()
        finally:
            self.transport.close()

    def _listen_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.transport.listen(stop, self.on_press)
            except Exception as error:
                logger.warning("%s connection dropped: %s", self.kind, error)
            stop.wait(5.0)
