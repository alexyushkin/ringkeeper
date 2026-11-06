"""Routines: a project's agent run with a prompt, on a schedule, an event, or by hand.

- A new routine starts paused. "Test run" runs it once (paused or not) so the
  owner sees the result before turning it on; "Run now" runs it any time.
- Each run is an ordinary task, run as the routine's agent: reads run, and
  actions wait for approval like any other (Approvals, the conversation's and
  task page's cards, notifications). Its limits: steps and minutes per run (a
  subscription isn't billed per task), plus a dollar cap in API key mode, and at
  most `max_runs_per_day` runs a day (the owner's day, in the schedule's zone).
- Nothing runs while the routine, its project, all routines (the tray's "Pause
  all routines") or everything ("Stop everything") is paused.
- Schedules (ringkeeper.schedules) run in the owner's time zone. After the
  computer slept, one run makes up for the missed ones and says how many.
- Events start one run each (`event_key`): a call to the routine's local
  webhook, a file added to the project's folder. Each run is recorded, started
  or skipped (and why), in the routine's history.
"""

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import AuthMode, Project, Routine, RoutineRun, Task
from ringkeeper.schedules import catch_up, describe, next_after, parse
from ringkeeper.services import agents, paging, projects, routine_events, tasks
from ringkeeper.services.runtime import effective

TRIGGERS = ("schedule", "event", "manual")
SOURCES = ("webhook", "folder", "airtable", "gmail")  # the last two: services.routine_events
NOTIFY = ("app", "channels", "none")
STARTED = ("schedule", "catch_up", "event", "manual")  # what counts toward the day's limit
MAX_FILES_PER_TICK = 20
FOLDER_SKIP = {".git", "node_modules", ".venv", "__pycache__", "previous-version"}

# Starting points for a new routine (the owner edits everything after).
TEMPLATES: dict[str, dict[str, Any]] = {
    "daily-digest": {
        "name": "Daily digest",
        "prompt": "Write a short digest of what changed in this project since yesterday "
        "({{today}}): new records, failed runs, anything that needs attention. Read only.",
        "trigger": "schedule",
        "schedule": {"every": "weekdays", "time": "08:00"},
    },
    "lead-follow-up": {
        "name": "New-lead follow-up draft",
        "prompt": "A new lead arrived: {{event}}. Draft a short, friendly follow-up email "
        "for my review. Save it as a draft; don't send anything.",
        "trigger": "event",
        "event": {"source": "webhook"},
    },
    "weekly-report": {
        "name": "Weekly report",
        "prompt": "Write this week's report for the client, up to {{today}}: what was "
        "done, numbers that moved, and what's next. Read only; put the report in a draft.",
        "trigger": "schedule",
        "schedule": {"every": "week", "days": ["fri"], "time": "16:00"},
    },
    "data-sync-check": {
        "name": "Data sync check",
        "prompt": "Check that the project's connected systems agree: compare record counts "
        "and the latest records, and list any differences. Read only; change nothing.",
        "trigger": "schedule",
        "schedule": {"every": "day", "time": "07:00"},
    },
}


@dataclass(frozen=True)
class RoutineIn:
    name: str
    agent: str
    prompt: str
    trigger: str
    schedule: dict[str, Any] | None = None
    event: dict[str, Any] | None = None
    budget_usd: float | None = None
    max_steps: int = 40
    max_minutes: int = 15
    max_runs_per_day: int = 10
    notify: str = "app"
    catch_up: bool = True


def _checked(data: RoutineIn, default_tz: str) -> tuple[RoutineIn, dict[str, Any] | None]:
    """The input, checked; and the schedule as stored (with its time zone)."""
    if not data.name.strip():
        raise RuleViolationError("Name the routine.")
    if not data.prompt.strip():
        raise RuleViolationError("Say what the agent should do (the prompt).")
    if data.trigger not in TRIGGERS:
        raise RuleViolationError(f"A routine runs on {', '.join(TRIGGERS)}.")
    if data.notify not in NOTIFY:
        raise RuleViolationError(f"Notify by {', '.join(NOTIFY)}.")
    if not 1 <= data.max_steps <= 500 or not 1 <= data.max_minutes <= 240:
        raise RuleViolationError("Steps are 1 to 500 and minutes 1 to 240 per run.")
    if not 1 <= data.max_runs_per_day <= 500:
        raise RuleViolationError("At most 1 to 500 runs a day.")
    if data.budget_usd is not None and data.budget_usd <= 0:
        raise RuleViolationError("A spending cap is more than $0 (or none).")
    schedule = None
    if data.trigger == "schedule":
        schedule = parse({"tz": default_tz, **(data.schedule or {})}).as_json()
    if data.trigger == "event":
        source = (data.event or {}).get("source")
        if source not in SOURCES:
            raise RuleViolationError(f"An event is one of {', '.join(SOURCES)}.")
    return data, schedule


def local_zone() -> str:
    """When the app doesn't say (it sends the owner's zone from the browser): UTC."""
    return "UTC"


def get_routine(session: Session, ident: str | uuid.UUID) -> Routine:
    try:
        found = session.get(Routine, uuid.UUID(str(ident)))
    except ValueError:
        found = None
    if found is None:
        raise NotFoundError(f"no routine '{ident}'")
    return found


def list_routines(
    session: Session, project: str | None = None, client: str | None = None
) -> list[Routine]:
    query = select(Routine).join(Project, Project.id == Routine.project_id)
    if project:
        query = query.where(Project.name == project)
    if client:
        query = query.where(Project.owner == client)
    return list(session.scalars(query.order_by(Project.name, Routine.name)))


def create_routine(
    session: Session, project: str, data: RoutineIn, *, tz: str | None = None
) -> Routine:
    """A new routine starts paused: test it, then turn it on."""
    found = projects.get_project(session, project)
    agent = agents.get_agent(session, project, data.agent)
    data, schedule = _checked(data, tz or local_zone())
    if data.trigger == "event" and _source(data.event) in routine_events.SOURCES:
        routine_events.check(found, dict(data.event or {}))
    routine = Routine(
        project_id=found.id, agent_id=agent.id, name=data.name.strip(),
        prompt=data.prompt.strip(), trigger=data.trigger, schedule=schedule,
        event=dict(data.event) if data.trigger == "event" and data.event else None,
        budget_usd=data.budget_usd, max_steps=data.max_steps, max_minutes=data.max_minutes,
        max_runs_per_day=data.max_runs_per_day, notify=data.notify, catch_up=data.catch_up,
        paused=True, state={},
        hook_secret=secrets.token_urlsafe(24) if _source(data.event) == "webhook" else None,
    )  # fmt: skip
    session.add(routine)
    session.flush()
    return routine


def update_routine(session: Session, ident: str, data: RoutineIn) -> Routine:
    routine = get_routine(session, ident)
    agent = agents.get_agent(session, routine.project.name, data.agent)
    zone = (routine.schedule or {}).get("tz") or local_zone()
    data, schedule = _checked(data, zone)
    if data.trigger == "event" and _source(data.event) in routine_events.SOURCES:
        routine_events.check(routine.project, dict(data.event or {}))
    changed_when = (data.trigger, schedule, data.event) != (
        routine.trigger,
        routine.schedule,
        routine.event,
    )
    routine.agent_id, routine.name, routine.prompt = agent.id, data.name.strip(), data.prompt
    routine.trigger, routine.schedule = data.trigger, schedule
    routine.event = dict(data.event) if data.trigger == "event" and data.event else None
    routine.budget_usd, routine.max_steps = data.budget_usd, data.max_steps
    routine.max_minutes, routine.max_runs_per_day = data.max_minutes, data.max_runs_per_day
    routine.notify, routine.catch_up = data.notify, data.catch_up
    if _source(routine.event) == "webhook" and not routine.hook_secret:
        routine.hook_secret = secrets.token_urlsafe(24)
    if changed_when:
        routine.state, routine.next_run_at = {}, None
        if not routine.paused:
            _schedule_next(routine, datetime.now(UTC))
    session.flush()
    return routine


def set_paused(session: Session, ident: str, paused: bool, now: datetime | None = None) -> Routine:
    """Turn a routine on (it runs from its next time; nothing missed is made up) or off."""
    routine = get_routine(session, ident)
    routine.paused = paused
    routine.next_run_at = None
    if not paused:
        _schedule_next(routine, now or datetime.now(UTC))
    session.flush()
    return routine


def delete_routine(session: Session, ident: str) -> None:
    session.delete(get_routine(session, ident))
    session.flush()


def describe_when(routine: Routine) -> str:
    """When it runs, in words."""
    if routine.trigger == "schedule" and routine.schedule:
        return describe(parse(routine.schedule))
    if routine.trigger == "event":
        source = _source(routine.event)
        return {
            "webhook": "When its webhook is called (from this computer)",
            "folder": "When a file is added to the project's folder",
            "airtable": f"When a record is added to {(routine.event or {}).get('table')}",
            "gmail": "When a matching email arrives",
        }.get(source or "", f"On {source} events")
    return "When you run it"


def _source(event: dict[str, Any] | None) -> str | None:
    return str(event.get("source")) if event and event.get("source") else None


def _schedule_next(routine: Routine, now: datetime) -> None:
    if routine.trigger == "schedule" and routine.schedule:
        routine.next_run_at = next_after(parse(routine.schedule), now)


# --- Runs ---


def render(routine: Routine, *, now: datetime, event: dict[str, Any] | None, missed: int) -> str:
    """The prompt with {{today}}, {{now}}, {{project}}, {{missed}} and {{event...}} filled."""
    zone = ZoneInfo((routine.schedule or {}).get("tz") or "UTC")
    local = now.astimezone(zone)
    values = {
        "today": local.date().isoformat(),
        "now": local.strftime("%Y-%m-%d %H:%M %Z"),
        "project": routine.project.name,
        "missed": str(missed),
        "event": _event_text(event),
    }
    for key, value in (event or {}).items():
        values[f"event.{key}"] = str(value)
    text = routine.prompt
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    if missed:
        text += (
            f"\n\n(The computer was asleep: this run makes up for {missed} missed run"
            f"{'s' if missed != 1 else ''}. Cover the whole time since the last run.)"
        )
    return text


def _event_text(event: dict[str, Any] | None) -> str:
    if not event:
        return "(no event)"
    return "; ".join(f"{k}: {str(v)[:500]}" for k, v in event.items())


def runs_today(session: Session, routine: Routine, now: datetime) -> int:
    zone = ZoneInfo((routine.schedule or {}).get("tz") or "UTC")
    midnight = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        session.scalar(
            select(func.count())
            .select_from(RoutineRun)
            .where(
                RoutineRun.routine_id == routine.id,
                RoutineRun.task_id.is_not(None),
                RoutineRun.trigger.in_(STARTED),
                RoutineRun.created_at >= midnight.astimezone(UTC),
            )
        )
        or 0
    )


@dataclass
class Started:
    run: RoutineRun
    task: Task | None = None
    notes: list[str] = field(default_factory=list[str])


def start_run(
    session: Session,
    settings: Settings,
    routine: Routine,
    trigger: str,
    *,
    now: datetime | None = None,
    event: dict[str, Any] | None = None,
    event_key: str | None = None,
    missed: int = 0,
    due_at: datetime | None = None,
) -> Started:
    """Run the routine once: a task run as its agent, within its limits. A run past the
    day's limit is recorded as skipped. An event already run (`event_key`) isn't run
    again: the earlier run is returned."""
    now = now or datetime.now(UTC)
    if event_key is not None:
        earlier = session.scalar(
            select(RoutineRun).where(
                RoutineRun.routine_id == routine.id, RoutineRun.event_key == event_key
            )
        )
        if earlier is not None:
            return Started(earlier)
    run = RoutineRun(
        routine_id=routine.id, trigger=trigger, event_key=event_key, event=event,
        missed=missed, due_at=due_at, created_at=now,
    )  # fmt: skip
    if trigger != "test" and runs_today(session, routine, now) >= routine.max_runs_per_day:
        run.skipped = f"today's limit of {routine.max_runs_per_day} runs was reached"
        return _record(session, run)
    current = effective(session, settings)
    budget = routine.budget_usd if current.claude_auth_mode is AuthMode.API_KEY else None
    task = tasks.create_task(
        session, settings, project=routine.project.name,
        prompt=render(routine, now=now, event=event, missed=missed),
        max_turns=routine.max_steps, max_runtime_s=routine.max_minutes * 60,
        budget_usd=budget, outcome=f"Routine: {routine.name}", agent_id=routine.agent_id,
    )  # fmt: skip
    run.task_id = task.id
    started = _record(session, run)
    started.task = task
    return started


def _record(session: Session, run: RoutineRun) -> Started:
    try:
        with session.begin_nested():
            session.add(run)
            session.flush()
    except IntegrityError:  # the same event, recorded meanwhile by another tick
        existing = session.scalar(
            select(RoutineRun).where(
                RoutineRun.routine_id == run.routine_id, RoutineRun.event_key == run.event_key
            )
        )
        if existing is None:
            raise
        return Started(existing)
    return Started(run)


def blocked(session: Session, settings: Settings, routine: Routine) -> str | None:
    """Why it can't run on its own now (a Test run or Run now still can)."""
    current = effective(session, settings)
    if current.paused:
        return "everything is stopped"
    if current.routines_paused:
        return "all routines are paused"
    if routine.project.paused:
        return f"{routine.project.title}'s routines are paused"
    if routine.paused:
        return "the routine is paused"
    return None


# --- The scheduler ---


def tick(session: Session, settings: Settings, now: datetime | None = None) -> list[Started]:
    """Start whatever is due: scheduled runs (one catch-up after sleep) and new files in
    project folders. Called every half minute by the scheduler."""
    now = now or datetime.now(UTC)
    started: list[Started] = []
    for routine in list_routines(session):
        if blocked(session, settings, routine) is not None:
            continue
        if routine.trigger == "schedule":
            started += _due(session, settings, routine, now)
        elif routine.trigger == "event" and _source(routine.event) == "folder":
            started += _new_files(session, settings, routine, now)
        elif routine.trigger == "event" and _source(routine.event) in routine_events.SOURCES:
            started += routine_events.poll(session, settings, routine, now)
    return started


def _due(session: Session, settings: Settings, routine: Routine, now: datetime) -> list[Started]:
    if not routine.schedule:
        return []
    if routine.next_run_at is None:
        _schedule_next(routine, now)
        return []
    caught = catch_up(parse(routine.schedule), routine.next_run_at, now)
    due = routine.next_run_at
    routine.next_run_at = caught.next_run
    if not caught.run:
        return []
    if caught.missed and not routine.catch_up:
        run = RoutineRun(
            routine_id=routine.id, trigger="catch_up", missed=caught.missed, due_at=due,
            skipped=f"missed {caught.missed + 1} runs while the computer was asleep "
            "(catching up is off)", created_at=now,
        )  # fmt: skip
        return [_record(session, run)]
    trigger = "catch_up" if caught.missed else "schedule"
    return [
        start_run(
            session,
            settings,
            routine,
            trigger,
            now=now,
            missed=caught.missed,
            due_at=due,
            event_key=f"due:{due.isoformat()}",
        )
    ]


def _new_files(
    session: Session, settings: Settings, routine: Routine, now: datetime
) -> list[Started]:
    """Files added to the project's folder since it last looked. The first look only
    notes what's there: files already in the folder aren't events."""
    folder = _folder(settings, routine.project)
    if folder is None:
        return []
    seen = set(routine.state.get("files") or [])
    current = _listing(folder)
    if "files" not in routine.state:
        routine.state = {**routine.state, "files": sorted(current)}
        return []
    started: list[Started] = []
    for path in sorted(current - seen)[:MAX_FILES_PER_TICK]:
        started.append(
            start_run(
                session,
                settings,
                routine,
                "event",
                now=now,
                event={"file": path},
                event_key=f"file:{path}",
            )
        )
    routine.state = {**routine.state, "files": sorted(current)}
    return started


def _folder(settings: Settings, project: Project) -> Path | None:
    if not project.workspace_path:
        return None
    path = Path(project.workspace_path)
    if not path.is_absolute() and settings.workspaces_root:
        path = Path(settings.workspaces_root) / path
    return path if path.is_dir() else None


def _listing(folder: Path, limit: int = 5000) -> set[str]:
    """The folder's files, relative, skipping what isn't the owner's (git, caches)."""
    found: set[str] = set()
    for path in folder.rglob("*"):
        if len(found) >= limit:
            break
        if any(part in FOLDER_SKIP for part in path.relative_to(folder).parts):
            continue
        if path.is_file():
            found.add(path.relative_to(folder).as_posix())
    return found


def hook(
    session: Session, settings: Settings, ident: str, secret: str, body: Any, key: str | None
) -> Started:
    """A call to a routine's webhook: one run, with the call's body as the event."""
    routine = get_routine(session, ident)
    if not routine.hook_secret or not secrets.compare_digest(routine.hook_secret, secret):
        raise NotFoundError("no routine with that webhook")
    if _source(routine.event) != "webhook":
        raise RuleViolationError("This routine doesn't run on a webhook.")
    event: dict[str, Any] = (
        cast(dict[str, Any], body) if isinstance(body, dict) else {"body": str(body)[:2000]}
    )
    if (why := blocked(session, settings, routine)) is not None:
        run = RoutineRun(
            routine_id=routine.id, trigger="event", event_key=key, skipped=why, event=event,
            created_at=datetime.now(UTC),
        )  # fmt: skip
        return _record(session, run)
    return start_run(session, settings, routine, "event", event=event, event_key=key)


# --- History ---


@dataclass(frozen=True)
class RunView:
    id: str
    trigger: str
    created_at: datetime
    missed: int
    skipped: str | None
    task_id: str | None
    status: str | None
    summary: str | None
    cost_usd: float | None
    steps: int | None
    error: str | None


def history(session: Session, ident: str, limit: int = 50) -> list[RunView]:
    return list(history_page(session, ident, limit).items)


def history_page(
    session: Session, ident: str, limit: int = 50, cursor: str | None = None
) -> paging.Page[RunView]:
    routine = get_routine(session, ident)
    page = paging.fetch(
        session, select(RoutineRun).where(RoutineRun.routine_id == routine.id),
        RoutineRun.created_at, RoutineRun.id, limit=limit, cursor=cursor,
    )  # fmt: skip
    views: list[RunView] = []
    for run in page.items:
        task = session.get(Task, run.task_id) if run.task_id else None
        cost = sum(s.cost_usd or 0 for s in task.sessions) if task else None
        steps = sum(s.turns or 0 for s in task.sessions) if task else None
        views.append(
            RunView(
                str(run.id),
                run.trigger,
                run.created_at,
                run.missed,
                run.skipped,
                str(task.id) if task else None,
                task.status.value if task else None,
                task.summary if task else None,
                round(cost, 4) if cost else None,
                steps,
                task.error if task else None,
            )
        )
    return paging.Page(views, page.next, page.total)


def last_runs(session: Session, routine_ids: list[uuid.UUID]) -> dict[uuid.UUID, RoutineRun]:
    latest: dict[uuid.UUID, RoutineRun] = {}
    for run in session.scalars(
        select(RoutineRun)
        .where(RoutineRun.routine_id.in_(routine_ids))
        .order_by(RoutineRun.created_at)
    ):
        latest[run.routine_id] = run
    return latest
