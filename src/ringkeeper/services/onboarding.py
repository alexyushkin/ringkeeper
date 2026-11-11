"""Home's first steps: the checklist until it's done (or dismissed), and requests to try.

The checklist is four things: Claude connected, a project, a connection, a task.
Suggestions are built from the user's own projects and connections, and only
ever read: listing, summarizing, explaining. Nothing in them changes anything.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.config import Settings
from ringkeeper.models import Instance, InstanceType, Project, ProjectInstance, Setting, Task

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
    InstanceType.MCP: "List what {connection} can do for {project}",
    InstanceType.SMTP: "Which address does {connection} send from?",
    InstanceType.RESEND: "List the sending domains on {connection}",
    InstanceType.MAILBOX: "Summarize the unread emails in {connection}",
    InstanceType.HTTP: "List what the {connection} API offers",
    InstanceType.S3: "List the files in {connection}",
    InstanceType.VENDOR: "List what {connection} can do for {project}",
    InstanceType.ORACLE: "List the tables in {project}'s database",
}
FOLDER = "Explain what's in {project}'s folder, in a few lines"
TASKS = "What did my tasks do this week?"


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
    return list(
        session.scalars(
            select(Instance)
            .where(Instance.all_projects.is_(False), Instance.id.not_in(linked))
            .order_by(Instance.name)
        )
    )


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
            if instance.shared or instance.type in seen_services:
                continue
            seen_services.add(instance.type)
            picked.append(
                BY_SERVICE[instance.type].format(
                    project=project.title, connection=instance.title or instance.name
                )
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
