"""Home's first steps: the checklist until it's done (or dismissed), and requests to try.

The checklist is four things: Claude connected, a project, a connection, a task.
Suggestions are built from the user's own projects and connections, and only
ever read: listing, summarizing, explaining. Nothing in them changes anything.
"""

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.config import Settings
from ringkeeper.models import Instance, InstanceType, Project, ProjectInstance, Setting, Task
from ringkeeper.services import instances

DISMISSED_KEY = "onboarding.checklist_dismissed"
TOUR_KEY = "onboarding.tour_seen"
MAX_SUGGESTIONS = 3

# One read-only request per kind of connection: {project} and {connection}.
BY_SERVICE: dict[InstanceType, str] = {
    InstanceType.SUPABASE: "List the tables in {project}'s database",
    InstanceType.NEON: "List the tables in {project}'s database",
    InstanceType.N8N: "List {project}'s n8n workflows and say which ones are active",
    InstanceType.AIRTABLE: "List the tables in {project}'s Airtable base",
    InstanceType.VERCEL: "Show {project}'s latest Vercel deployments and whether they worked",
    InstanceType.GITHUB: "List the open pull requests in {project}'s GitHub repository",
    InstanceType.GOOGLE: "Summarize this week's events on {connection}'s calendar",
    InstanceType.GMAIL: "Summarize the unread emails in {connection}",
    InstanceType.TWILIO: "List the last 10 text messages {connection} sent or received",
    InstanceType.MCP: "In {project}, use {connection} to list the tools it offers",
    InstanceType.SMTP: "Which address does {connection} send from?",
    InstanceType.RESEND: "List the sending domains on {connection}",
    InstanceType.MAILBOX: "Summarize the unread emails in {connection}",
    InstanceType.HTTP: "List what the {connection} API offers",
    InstanceType.S3: "List the files in {connection}",
    InstanceType.VENDOR: "In {project}, use {connection} to list the tools it offers",
    InstanceType.ORACLE: "List the tables in {project}'s database",
}
# A Google account's, by what it was given (Instance.config["access"], as google.config_access
# reads it): the first of these it has. One given only mail gets a mail request, say.
GOOGLE_BY_ACCESS: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"calendar_read", "calendar_write"}), BY_SERVICE[InstanceType.GOOGLE]),
    (frozenset({"mail_read"}), "Summarize the unread emails in {connection}"),
    (frozenset({"drive_files", "drive_read_all"}), "List the latest files in {connection}'s Drive"),
    (frozenset({"search_console_read"}), "List the sites {connection} has in Search Console"),
    (frozenset({"analytics_read"}), "List the Analytics properties {connection} can read"),
    (
        frozenset({"youtube_read", "youtube_manage"}),
        "List the latest videos on {connection}'s YouTube channel",
    ),
)
FOLDER = "Explain what's in {project}'s folder, in a few lines"
TASKS = "What did my tasks do this week?"


def request(instance: Instance) -> str | None:
    """The read-only request to try on a connection, with {project} and {connection} to fill
    in. None: it has none (a Google account given nothing it can read, say)."""
    if instance.type is not InstanceType.GOOGLE:
        return BY_SERVICE.get(instance.type)
    given = {str(a) for a in cast(list[Any], (instance.config or {}).get("access") or [])}
    if not given:  # from before access was recorded
        return BY_SERVICE[InstanceType.GOOGLE]
    return next((asks for needs, asks in GOOGLE_BY_ACCESS if needs & given), None)


@dataclass(frozen=True)
class Checklist:
    claude: bool
    project: bool
    connection: bool
    task: bool
    dismissed: bool
    # Connections no task can use: linked to no project (and not given to all of them).
    unlinked: tuple[str, ...] = ()

    @property
    def done(self) -> bool:
        return self.claude and self.project and self.connection and self.task and not self.unlinked


def unlinked(session: Session) -> list[Instance]:
    """Connections linked to no project, so no task can use them (the demo's are linked)."""
    linked = select(ProjectInstance.instance_id)
    found = session.scalars(
        select(Instance)
        .where(Instance.all_projects.is_(False), Instance.id.not_in(linked))
        .order_by(Instance.name)
    )
    return [i for i in found if not instances.notes_only(i)]  # memory's: never linked


def checklist(session: Session, claude_ready: bool) -> Checklist:
    def count(query: object) -> int:
        return session.scalar(query) or 0  # type: ignore[arg-type]

    return Checklist(
        claude=claude_ready,
        project=count(select(func.count()).select_from(Project)) > 0,
        # A client's connection: a shared tool alone (files, a browser) isn't one.
        connection=count(
            select(func.count()).select_from(Instance).where(Instance.shared.is_(False))
        )
        > 0,
        task=count(select(func.count()).select_from(Task)) > 0,
        dismissed=session.get(Setting, DISMISSED_KEY) is not None,
        unlinked=tuple(i.name for i in unlinked(session)),
    )


def _mark(session: Session, key: str) -> None:
    if session.get(Setting, key) is None:
        session.add(Setting(key=key, value={"at": True}))
        session.flush()


def dismiss(session: Session) -> None:
    _mark(session, DISMISSED_KEY)


WHATS_NEW_KEY = "onboarding.whats_new"


def keep_notes(session: Session, version: str, notes: str) -> None:
    """An update was downloaded: its notes show on Home once it's running."""
    row = session.get(Setting, WHATS_NEW_KEY)
    value = {"version": version, "notes": notes, "seen": False}
    if row is None:
        session.add(Setting(key=WHATS_NEW_KEY, value=value))
    elif row.value.get("version") != version or row.value.get("notes") != notes:
        row.value = value
    session.flush()


def whats_new(session: Session, running: str | None) -> dict[str, str] | None:
    """The notes of the version now running, until they're dismissed."""
    row = session.get(Setting, WHATS_NEW_KEY)
    if (
        running is None
        or row is None
        or row.value.get("seen")
        or row.value.get("version") != running
    ):
        return None
    return {"version": str(row.value["version"]), "notes": str(row.value.get("notes") or "")}


def whats_new_seen(session: Session) -> None:
    row = session.get(Setting, WHATS_NEW_KEY)
    if row is not None:
        row.value = {**row.value, "seen": True}
        session.flush()


def tour_seen(session: Session) -> bool:
    return session.get(Setting, TOUR_KEY) is not None


def see_tour(session: Session) -> None:
    _mark(session, TOUR_KEY)


def suggestions(session: Session, settings: Settings) -> list[str]:
    """Up to three read-only requests from the user's projects and connections, the
    most recently changed first. A project with no connection gets its folder
    (desktop) instead; with a task done, one asks what the tasks did."""
    found = session.scalars(
        select(Project)
        .options(selectinload(Project.bindings).selectinload(ProjectInstance.instance))
        .order_by(Project.updated_at.desc())
    ).all()
    picked: list[str] = []
    seen_services: set[InstanceType] = set()
    for project in found:
        for binding in sorted(project.bindings, key=lambda b: b.instance.name):
            instance = binding.instance
            asks = request(instance)
            if asks is None or instance.shared or instance.type in seen_services:
                continue
            if instances.notes_only(instance):  # it holds notes: no task calls it
                continue
            seen_services.add(instance.type)
            picked.append(
                asks.format(project=project.title, connection=instance.title or instance.name)
            )
            break
        if len(picked) >= MAX_SUGGESTIONS - 1:
            break
    if found and len(picked) < MAX_SUGGESTIONS - 1 and settings.mode == "desktop":
        unlisted = next(
            (p for p in found if not any(not b.instance.shared for b in p.bindings)), found[0]
        )
        picked.append(FOLDER.format(project=unlisted.title))
    if session.scalar(select(func.count()).select_from(Task)):
        picked.append(TASKS)
    return picked[:MAX_SUGGESTIONS]
