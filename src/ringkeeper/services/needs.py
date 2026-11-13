"""What kind of connection a request can't be done without, and whether a project has one.

The router says it in the same call that routes the request (RoutedTask.needs: "meta_ads",
"email"...). This is only the lookup from that kind to the connections that give it: a
project with none of them gets "This project has no Meta Ads connection" and a button to add
one, instead of a task that reads the repository and ends Not done. A worker that ends Not
done says the same kinds (services.link_offer).

A connection Ringkeeper can't tell the kind of (any MCP server, any HTTP API) counts when its
name says so ("postgres", "stripe"), so a project that has one isn't stopped.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ringkeeper import remote
from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    Complexity,
    Instance,
    InstanceType,
    Message,
    Project,
    Task,
    TaskKind,
    TaskRisk,
)
from ringkeeper.services import titles


@dataclass(frozen=True)
class Need:
    label: str  # as a sentence says it: "This project has no {label} connection."
    types: tuple[InstanceType, ...] = ()  # Ringkeeper's own connections that give it
    remotes: tuple[str, ...] = ()  # Recommended services (ringkeeper.remote keys)
    vendors: tuple[str, ...] = ()  # business systems (adapters.vendors keys)
    words: tuple[str, ...] = ()  # in another connection's name: it counts too
    # What "Add a ... connection" opens: the first of these the catalog has (a Recommended
    # service, a business system, or a built-in kind). Empty: the catalog.
    add: tuple[str, ...] = ()


ADS = ("meta_ads", "pipeboard")
PAYMENTS = ("stripe", "paypal", "mollie", "paddle", "gocardless")

NEEDS: dict[str, Need] = {
    "meta_ads": Need(
        "Meta Ads", remotes=ADS, words=("meta-ads", "facebook-ads", "meta ads", "facebook ads"),
        add=ADS,
    ),
    "google_ads": Need(
        "Google Ads", vendors=("google_ads",), words=("google-ads", "google ads", "adwords"),
        add=("google_ads",),
    ),
    "ads": Need(
        "ads", remotes=ADS, vendors=("google_ads",),
        words=("meta-ads", "facebook-ads", "google-ads", "adwords", " ads"), add=ADS,
    ),
    "email": Need(
        "email",
        types=(InstanceType.GOOGLE, InstanceType.GMAIL, InstanceType.MAILBOX, InstanceType.SMTP,
               InstanceType.RESEND),
        vendors=("microsoft",), words=("mail", "smtp", "outlook"), add=("mailbox",),
    ),
    "calendar": Need(
        "calendar", types=(InstanceType.GOOGLE,), remotes=("calcom",), vendors=("microsoft",),
        words=("calendar", "cal.com", "calendly"), add=("google", "calcom"),
    ),
    "database": Need(
        "database",
        types=(InstanceType.SUPABASE, InstanceType.NEON, InstanceType.ORACLE),
        remotes=("neon", "supabase"),
        words=("postgres", "mysql", "sql", "database", "mongo", "redis", "-db", " db"),
        add=("supabase",),
    ),
    "payments": Need(
        "payments", remotes=PAYMENTS,
        vendors=("square", "helcim", "lemonsqueezy", "clover", "braintree"),
        words=("stripe", "paypal", "payment", "square", "mollie", "paddle"), add=("stripe",),
    ),
    # What a worker can also say it lacked (services.link_offer): no check before starting.
    "google": Need("Google", types=(InstanceType.GOOGLE,)),
    "airtable": Need("Airtable", types=(InstanceType.AIRTABLE,), remotes=("airtable",)),
    "n8n": Need("n8n", types=(InstanceType.N8N,)),
    "github": Need("GitHub", types=(InstanceType.GITHUB,), remotes=("github",)),
    "vercel": Need("Vercel", types=(InstanceType.VERCEL,)),
    "slack": Need("Slack", words=("slack",)),
}  # fmt: skip

# The kinds the router may name: work in an outside account that no code change can do.
ROUTED = ("ads", "meta_ads", "google_ads", "email", "calendar", "database", "payments")


UNTOLD = frozenset({InstanceType.MCP, InstanceType.HTTP})


def need(key: str) -> Need | None:
    return NEEDS.get(key.strip().lower())


def fits(instance: Instance, key: str) -> bool:
    """Whether this connection gives what `key` needs."""
    found = need(key)
    if found is None:
        return False
    config = cast(Mapping[str, Any], instance.config or {})
    service = config.get("remote") if instance.type is InstanceType.MCP else None
    if instance.type is InstanceType.VENDOR:
        service = config.get("vendor")
    known = instance.type in found.types or service in (*found.remotes, *found.vendors)
    # Only a connection Ringkeeper can't tell the kind of (any MCP server, any HTTP API) is
    # judged by its name: a Recommended service or a business system is what its key says.
    if known or service or instance.type not in UNTOLD:
        return known
    named = " ".join(
        str(part)
        for part in (instance.name, instance.title, config.get("registry"), config.get("preview"))
        if part
    ).lower()
    return any(word in f" {named}" for word in found.words)


def _project(session: Session, name: str) -> Project | None:
    return session.scalar(
        select(Project).options(selectinload(Project.bindings)).where(Project.name == name)
    )


def missing(session: Session, project: str, keys: Iterable[str]) -> str | None:
    """The first of `keys` (the router's, for a task) that `project` has no connection for."""
    found = _project(session, project)
    if found is None:
        return None
    linked = [b.instance for b in found.bindings]
    for key in keys:
        if need(key) is not None and not any(fits(i, key) for i in linked):
            return key.strip().lower()
    return None


def add_target(key: str) -> str:
    """What "Add a ... connection" opens in the catalog, or "" for the catalog itself."""
    from ringkeeper.adapters import vendor  # noqa: PLC0415 (the vendor tables are big)

    found = need(key)
    for target in found.add if found is not None else ():
        if target in remote.BY_KEY or target in vendor.VENDORS or target in BUILT_IN:
            return target
    return ""


BUILT_IN = frozenset(t.value for t in InstanceType)


def add_href(key: str, client: str, project: str, back: str = "", run: str = "") -> str:
    """The add form, for that client, linking what's added to the project. `back`: where to
    return, `run`: the message to run once it's added."""
    # `link`, not `project`: the app's own client and project picker owns those two.
    params = {"client": client, "link": project, "need": key}
    if target := add_target(key):
        params["service"] = target
    if back:
        params["back"] = back
    if run:
        params["run"] = run
    return f"/connections/new?{urlencode(params)}"


def no_connection(key: str) -> str:
    found = need(key)
    return f"This project has no {found.label if found else key} connection."


def add_label(key: str, project_title: str) -> str:
    found = need(key)
    what = found.label if found else key
    article = "an" if what[:1].lower() in "aeiou" else "a"
    return f"Add {article} {what} connection to {project_title}"


def entry(
    session: Session, message_id: uuid.UUID, project: str, task: Mapping[str, Any], key: str
) -> dict[str, Any]:
    """A task not started for want of a connection: what to say, the add form for its client
    (linking what's added to the project), and the task itself, to run once it's there."""
    owner = session.scalar(select(Project.owner).where(Project.name == project)) or ""
    title = titles.of(session, project)
    return {
        "message": str(message_id),
        "project": project,
        "title": title,
        "need": key,
        "says": no_connection(key),
        "label": add_label(key, title),
        "href": add_href(key, owner, project, run=str(message_id)),
        "task": dict(task),
    }


def run_waiting(session: Session, settings: Settings, message_id: str) -> list[Task]:
    """The tasks a request held back for a missing connection, started now that the project
    has one. A project that still has none says so, and nothing starts."""
    from ringkeeper.services import tasks, threads  # noqa: PLC0415 (tasks imports a lot)

    message = session.get(Message, uuid.UUID(message_id))
    routing = dict(message.routing or {}) if message is not None else {}
    waiting = cast(list[dict[str, Any]], routing.get("missing") or [])
    if message is None or not waiting:
        raise RuleViolationError("Nothing is waiting for a connection here any more.")
    for held in waiting:
        if missing(session, str(held["project"]), [str(held["need"])]) is not None:
            raise RuleViolationError(
                no_connection(str(held["need"]))
                .replace("This project", str(held["title"]), 1)
                .replace(" connection.", " connection yet.")
            )
    started: list[Task] = []
    for held in waiting:
        fields = cast(dict[str, Any], held["task"])
        task = tasks.create_task(
            session, settings, project=str(held["project"]),
            prompt=str(fields.get("prompt") or message.body),
            kind=TaskKind(fields["kind"]) if fields.get("kind") else None,
            risk=TaskRisk(fields["risk"]) if fields.get("risk") else None,
            complexity=Complexity(fields["complexity"]) if fields.get("complexity") else None,
            outcome=fields.get("outcome"), thread_id=message.thread_id, message_id=message.id,
            general=bool(fields.get("general")),
        )  # fmt: skip
        started.append(task)
        threads.add_reply(session, message.thread_id, threads.working_on(session, task))
    for reply in session.scalars(
        select(Message).where(Message.thread_id == message.thread_id, Message.id != message.id)
    ):
        shown = cast(list[dict[str, Any]], (reply.routing or {}).get("missing") or [])
        if any(m.get("message") == str(message.id) for m in shown):
            reply.routing = {**(reply.routing or {}), "missing": [], "ran": True}
    routing["missing"] = []
    routing["tasks"] = [*cast(list[str], routing.get("tasks") or []), *(str(t.id) for t in started)]
    message.routing = routing
    return started
