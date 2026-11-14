"""What kind of connection a request can't be done without, and whether a project has one.

The router says it in the same call that routes the request: the kinds of connection
(RoutedTask.needs: "meta_ads", "email", "records"...), the service the message names
(RoutedTask.service: "Airtable"), and the project's connections the work will use
(RoutedTask.connections). This is only the lookup from those to the project's links.

A false "missing" is worse than a task that ends Not done, so a request is held only when
no linked connection could do it:
- a linked connection the routing call picked, or that its task prompt names, settles it;
- a kind is met by any connection that can do the job (tables and records: Airtable, a
  database, Notion, a spreadsheet, a CRM);
- a service the message names is met only by that service;
- a connection Ringkeeper can't tell the kind of (any MCP server, any HTTP API) counts
  unless its name says it's something else.

A project with none gets "This project has no Meta Ads connection. It has Airtable and
Google, and none of them can do Meta Ads." and a button to add one. A worker that ends Not
done says the same kinds (services.link_offer).
"""

import re
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
    does: str = ""  # "none of them can do {does}"; the label when empty


ADS = ("meta_ads", "pipeboard")
PAYMENTS = ("stripe", "paypal", "mollie", "paddle", "gocardless")
# Tables and records: Airtable, a database, Notion, a spreadsheet, a CRM.
RECORD_TYPES = (
    InstanceType.AIRTABLE,
    InstanceType.SUPABASE,
    InstanceType.NEON,
    InstanceType.ORACLE,
    InstanceType.GOOGLE,
)
RECORD_REMOTES = (
    "airtable",
    "notion",
    "neon",
    "supabase",
    "attio",
    "monday",
    "odoo",
    "zoho_crm",
    "close",
    "gohighlevel",
)
RECORD_VENDORS = ("hubspot", "salesforce", "pipedrive", "microsoft")
RECORD_WORDS = (
    "airtable",
    "notion",
    "sheet",
    "excel",
    "table",
    "postgres",
    "mysql",
    "sql",
    "database",
    "mongo",
    "redis",
    "-db",
    " db",
    "crm",
    "baserow",
    "nocodb",
    "supabase",
)

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
        vendors=("microsoft",), words=("mail", "smtp", "outlook", "imap"), add=("mailbox",),
    ),
    "calendar": Need(
        "calendar", types=(InstanceType.GOOGLE,), remotes=("calcom",), vendors=("microsoft",),
        words=("calendar", "cal.com", "calendly"), add=("google", "calcom"), does="calendars",
    ),
    "records": Need(
        "database or spreadsheet", types=RECORD_TYPES, remotes=RECORD_REMOTES,
        vendors=RECORD_VENDORS,
        words=RECORD_WORDS, add=("airtable",), does="tables and records",
    ),
    # Said by routes made before "records": its data, so the same connections meet it.
    "database": Need(
        "database", types=RECORD_TYPES, remotes=RECORD_REMOTES, vendors=RECORD_VENDORS,
        words=RECORD_WORDS, add=("supabase",), does="tables and records",
    ),
    "spreadsheet": Need(
        "spreadsheet", types=(InstanceType.GOOGLE, InstanceType.AIRTABLE),
        remotes=("airtable",), vendors=("microsoft",),
        words=("sheet", "excel", "airtable", "spreadsheet"), add=("google",),
        does="spreadsheets",
    ),
    "files": Need(
        "files", types=(InstanceType.GOOGLE, InstanceType.S3), vendors=("microsoft",),
        words=("drive", "dropbox", "box", "files", "storage", "s3", "sharepoint", "onedrive",
               "blob"),
        add=("google", "s3"),
    ),
    "payments": Need(
        "payments", remotes=PAYMENTS,
        vendors=("square", "helcim", "lemonsqueezy", "clover", "braintree"),
        words=("stripe", "paypal", "payment", "square", "mollie", "paddle"), add=("stripe",),
    ),
    # What a worker can also say it lacked (services.link_offer), and services a message names.
    "google": Need(
        "Google", types=(InstanceType.GOOGLE,), words=("google", "gmail"), add=("google",)
    ),
    "airtable": Need(
        "Airtable", types=(InstanceType.AIRTABLE,), remotes=("airtable",), words=("airtable",),
        add=("airtable",),
    ),
    "notion": Need("Notion", remotes=("notion",), words=("notion",), add=("notion",)),
    "n8n": Need("n8n", types=(InstanceType.N8N,), words=("n8n",), add=("n8n",)),
    "github": Need(
        "GitHub", types=(InstanceType.GITHUB,), remotes=("github",), words=("github",),
        add=("github",),
    ),
    "vercel": Need("Vercel", types=(InstanceType.VERCEL,), words=("vercel",), add=("vercel",)),
    "slack": Need("Slack", words=("slack",)),
    "stripe": Need("Stripe", remotes=("stripe",), words=("stripe",), add=("stripe",)),
    "microsoft": Need(
        "Microsoft 365", vendors=("microsoft",),
        words=("microsoft", "outlook", "office", "onedrive", "sharepoint", "teams"),
        add=("microsoft",),
    ),
}  # fmt: skip

# A service as a message may name it, to the key above (or a Recommended service or business
# system key).
SERVICE_WORDS = {
    "gmail": "google", "google sheets": "google", "sheets": "google", "google drive": "google",
    "drive": "google", "google calendar": "google", "outlook": "microsoft", "excel": "microsoft",
    "onedrive": "microsoft", "sharepoint": "microsoft", "teams": "microsoft",
    "office 365": "microsoft", "microsoft 365": "microsoft", "facebook ads": "meta_ads",
    "instagram ads": "meta_ads", "meta": "meta_ads", "adwords": "google_ads",
    "cal.com": "calcom", "postgres": "database", "postgresql": "database", "mysql": "database",
}  # fmt: skip

# The kinds the router may name: work in an outside account that no code change can do.
ROUTED = (
    "ads", "meta_ads", "google_ads", "email", "calendar", "records", "spreadsheet", "files",
    "database", "payments",
)  # fmt: skip


UNTOLD = frozenset({InstanceType.MCP, InstanceType.HTTP})


def need(key: str) -> Need | None:
    return NEEDS.get(key.strip().lower())


def _service_of(instance: Instance) -> str | None:
    config = cast(Mapping[str, Any], instance.config or {})
    if instance.type is InstanceType.MCP:
        return cast(str | None, config.get("remote"))
    if instance.type is InstanceType.VENDOR:
        return cast(str | None, config.get("vendor"))
    return None


def _named(instance: Instance) -> str:
    config = cast(Mapping[str, Any], instance.config or {})
    parts = (instance.name, instance.title, config.get("registry"), config.get("preview"))
    return " " + " ".join(str(part) for part in parts if part).lower()


def _says_other(named: str, key: str) -> bool:
    """Whether a connection's name says it's some other kind than `key`."""
    mine = need(key)
    own: set[str] = set(mine.words) if mine is not None else set()
    return any(
        word in named and word not in own
        for other, found in NEEDS.items()
        if other != key
        for word in found.words
    )


def fits(instance: Instance, key: str) -> bool:
    """Whether this connection gives what `key` needs (a kind, or a service by its key)."""
    found = need(key)
    if found is None:
        return False
    service = _service_of(instance)
    known = (
        instance.type in found.types
        or service in (*found.remotes, *found.vendors)
        or (service is not None and service == key.strip().lower())
    )
    # Only a connection Ringkeeper can't tell the kind of (any MCP server, any HTTP API) is
    # judged by its name: a Recommended service or a business system is what its key says.
    if known or service or instance.type not in UNTOLD:
        return known
    named = _named(instance)
    if any(word in named for word in found.words):
        return True
    # Its name says nothing: it might do the job, and a task that ends Not done is better
    # than a request held for nothing.
    return not _says_other(named, key)


def service_key(said: str) -> str:
    """A service as a message names it ("Airtable", "Gmail") as a key here, or ""."""
    word = " ".join(said.strip().lower().replace("_", " ").split())
    if not word:
        return ""
    if word in SERVICE_WORDS:
        return SERVICE_WORDS[word]
    joined = word.replace(" ", "_")
    if joined in NEEDS:
        return joined
    from ringkeeper.adapters import vendor  # noqa: PLC0415 (the vendor tables are big)

    if joined in remote.BY_KEY or joined in vendor.VENDORS:
        return joined
    return ""


def _serves(instance: Instance, key: str) -> bool:
    """Whether this connection is the service `key` (one a message named)."""
    if need(key) is not None:
        return fits(instance, key)
    service = _service_of(instance)
    if service is not None:
        return service == key
    if instance.type not in UNTOLD:
        return False
    named = _named(instance)
    return key.replace("_", " ") in named or key in named or not _says_other(named, key)


def _project(session: Session, name: str) -> Project | None:
    return session.scalar(
        select(Project).options(selectinload(Project.bindings)).where(Project.name == name)
    )


def _mentions(prompt: str, instance: Instance) -> bool:
    """Whether a task's prompt names this connection (by its name, as the router saw it)."""
    name = instance.name.lower()
    return len(name) > 2 and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", prompt) is not None


def missing(session: Session, project: str, task: Mapping[str, Any]) -> str | None:
    """What `project` has no connection for, of what the router said a task needs, or None.

    `task`: the router's fields for it (`needs`, `service`, `connections`, `prompt`)."""
    found = _project(session, project)
    if found is None:
        return None
    linked = [b.instance for b in found.bindings]
    chosen = {str(name) for name in cast(list[Any], task.get("connections") or [])}
    prompt = str(task.get("prompt") or "").lower()
    # The routing call picked one of the project's own connections for it: nothing's missing.
    if any(i.name in chosen or _mentions(prompt, i) for i in linked):
        return None
    if key := service_key(str(task.get("service") or "")):
        # The message named the service: only that one does.
        return None if any(_serves(i, key) for i in linked) else key
    for said in cast(list[Any], task.get("needs") or []):
        kind = str(said).strip().lower()
        if need(kind) is not None and not any(fits(i, kind) for i in linked):
            return kind
    return None


def missing_kinds(session: Session, project: str, keys: Iterable[str]) -> str | None:
    """The first of these kinds `project` has no connection for (a worker's Not done)."""
    return missing(session, project, {"needs": list(keys)})


def still_missing(session: Session, project: str, key: str) -> bool:
    """Whether `project` still has no connection for what was missing (a kind or a service)."""
    if need(key) is not None:
        return missing_kinds(session, project, [key]) is not None
    found = _project(session, project)
    return found is not None and not any(_serves(b.instance, key) for b in found.bindings)


def add_target(key: str) -> str:
    """What "Add a ... connection" opens in the catalog, or "" for the catalog itself."""
    from ringkeeper.adapters import vendor  # noqa: PLC0415 (the vendor tables are big)

    found = need(key)
    for target in (*found.add, key) if found is not None else (key,):
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


def label(key: str) -> str:
    """How a sentence names it: "Meta Ads", "email", or a service's own name."""
    found = need(key)
    if found is not None:
        return found.label
    from ringkeeper.adapters import vendor  # noqa: PLC0415 (the vendor tables are big)

    if (service := remote.BY_KEY.get(key)) is not None:
        return service.name
    if (system := vendor.VENDORS.get(key)) is not None:
        return system.name
    return key.replace("_", " ")


def no_connection(key: str) -> str:
    return f"This project has no {label(key)} connection."


def _short(instance: Instance) -> str:
    """A connection as a list names it: its title's first part ("Airtable" of "Airtable · Harbor
    Bakery"), or its name."""
    return (instance.title or "").split(" · ")[0].strip() or instance.name


def _listed(names: list[str], limit: int = 6) -> str:
    if len(names) > limit:
        names = [*names[:limit], f"{len(names) - limit} more"]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def what_it_has(session: Session, project: str, key: str) -> str:
    """The reply: what was looked for and what the project has, so it's plain why nothing
    started ("This project has no Meta Ads connection. It has Airtable, Google and n8n, and
    none of them can do Meta Ads.")."""
    found = _project(session, project)
    shorts = (_short(b.instance) for b in found.bindings) if found else ()
    names = sorted(dict.fromkeys(shorts), key=str.lower)
    said = no_connection(key)
    if not names:
        return f"{said[:-1]}, and no connections linked at all."
    if key not in ROUTED:  # a service the message named
        if len(names) == 1:
            return f"{said} It has {names[0]}, which isn't {label(key)}."
        return f"{said} It has {_listed(names)}, and none of them is {label(key)}."
    kind = need(key)
    does = kind.does if kind is not None and kind.does else label(key)
    if len(names) == 1:
        return f"{said} It has {names[0]}, which can't do {does}."
    return f"{said} It has {_listed(names)}, and none of them can do {does}."


def add_label(key: str, project_title: str) -> str:
    what = label(key)
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
        "says": what_it_has(session, project, key),
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
        if still_missing(session, str(held["project"]), str(held["need"])):
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
