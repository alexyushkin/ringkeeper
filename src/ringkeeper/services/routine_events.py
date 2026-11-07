"""Routines' events from connected services, and Automation watch.

A new Airtable record or Gmail message; a failed n8n run, a failed Vercel deploy, a failed
GitHub check or a new issue; or a website that stops answering. Each is checked every few
minutes, reading only. The first look notes what's there (it isn't an event); after that,
each new one starts one run of the routine (services.routines.start_run, keyed by its id),
with its fields as {{event...}}: `what` says it in a sentence, `link` points at it.

    {"source": "airtable", "connection": "acme-airtable", "base": "app...", "table": "Leads"}
    {"source": "gmail", "connection": "acme-info", "query": "from:@acme.example"}
    {"source": "n8n", "connection": "acme-n8n", "workflow": "optional workflow id"}
    {"source": "vercel", "connection": "acme-vercel", "project": "acme-site"}
    {"source": "github", "connection": "acme-github", "repo": "acme/site", "watch": "checks"}
    {"source": "website", "url": "https://acme.example"}

A service is read through the gateway, as the routine's project (session `watch:<id>`):
only the project's own connections, the same-client rule on every call, secrets scrubbed,
plan limits. A website is fetched directly: there's no connection or key, and all that's
kept is whether it answered.

The scheduler fetches first, with no database transaction open (a refused call writes to the
audit log, which a held write lock would block), then applies what it found in its own.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import httpx2 as httpx
from sqlalchemy.orm import Session

from ringkeeper.adapters import ADAPTERS
from ringkeeper.adapters.base import JSON, ToolKind
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import InstanceType, Project, Routine
from ringkeeper.vault import get_vault

POLL_EVERY = timedelta(minutes=5)
OVERLAP = timedelta(minutes=10)  # Airtable: look a little further back each time
SEEN_KEPT = 500
TIMEOUT_SECONDS = 60.0
WEBSITE_SECONDS = 15.0
WATCH = "watch:"  # the polls' session id, in the audit log

# The connection each source reads (a website has none).
SOURCES: dict[str, InstanceType | None] = {
    "airtable": InstanceType.AIRTABLE,
    "gmail": InstanceType.GOOGLE,
    "n8n": InstanceType.N8N,
    "vercel": InstanceType.VERCEL,
    "github": InstanceType.GITHUB,
    "website": None,
}
KIND_WORDS = {
    InstanceType.AIRTABLE: "an Airtable", InstanceType.GOOGLE: "a Google account",
    InstanceType.N8N: "an n8n", InstanceType.VERCEL: "a Vercel", InstanceType.GITHUB: "a GitHub",
}  # fmt: skip
WATCHES = ("checks", "issues")  # GitHub
FAILED_CHECKS = {"failure", "timed_out", "action_required", "startup_failure"}
WEBSITE_TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests answer for the website

Item = tuple[str, dict[str, Any]]  # (id, fields)


class Read(Protocol):
    """One read tool on the event's connection, through the gateway."""

    def __call__(self, tool: str, **args: Any) -> Awaitable[JSON]: ...


# Tests swap these for fakes: (read, event) -> new items. The event carries `_since` (when
# the last look was) and `_state` (what the routine keeps between looks).
Poller = Callable[[Read, dict[str, Any]], Coroutine[Any, Any, list[Item]]]


def check(project: Project, event: dict[str, Any]) -> None:
    """The event's connection is one of the project's, of the right kind; and it says what to
    watch."""
    source = str(event.get("source"))
    wanted = SOURCES[source]
    if wanted is not None:
        name = str(event.get("connection") or "")
        linked = {b.instance.name: b.instance for b in project.bindings}
        if name not in linked or linked[name].type is not wanted:
            raise RuleViolationError(
                f"Choose {KIND_WORDS[wanted]} connection linked to {project.name}."
            )
    if source == "airtable" and not (event.get("base") and event.get("table")):
        raise RuleViolationError("Say which Airtable base and table to watch.")
    if source == "vercel" and not event.get("project"):
        raise RuleViolationError("Say which Vercel project to watch.")
    if source == "github":
        if "/" not in str(event.get("repo") or ""):
            raise RuleViolationError("Say which repository to watch, as owner/name.")
        if event.get("watch") not in WATCHES:
            raise RuleViolationError("Watch the repository's checks or its new issues.")
    if source == "website":
        url = urlsplit(str(event.get("url") or ""))
        if url.scheme not in ("http", "https") or not url.hostname:
            raise RuleViolationError("Give the website's address, starting with https://.")


# --- Each source's look ---


async def _airtable(read: Read, event: dict[str, Any]) -> list[Item]:
    since = str(event.get("_since"))
    formula = f"IS_AFTER(CREATED_TIME(), DATETIME_PARSE('{since}'))"
    page = await read(
        "list_records", base_id=event["base"], table=event["table"], filter_formula=formula,
        page_size=50,
    )  # fmt: skip
    records = cast(list[JSON], page.get("records") or [])
    return [(str(r["id"]), cast(dict[str, Any], r.get("fields") or {})) for r in records]


async def _gmail(read: Read, event: dict[str, Any]) -> list[Item]:
    query = " ".join(filter(None, [str(event.get("query") or ""), "newer_than:2d"]))
    found = await read("search_mail", query=query, limit=25)
    messages = cast(list[JSON], found.get("messages") or [])
    return [
        (str(m["id"]), {k: m.get(k, "") for k in ("from", "to", "subject", "date", "snippet")})
        for m in messages
    ]


async def _n8n(read: Read, event: dict[str, Any]) -> list[Item]:
    args: dict[str, Any] = {"status": "error", "limit": 20}
    if event.get("workflow"):
        args["workflow_id"] = str(event["workflow"])
    page = await read("list_executions", **args)
    runs = [  # the filter is the server's; a run that says otherwise isn't a failure
        r for r in cast(list[JSON], page.get("executions") or [])
        if r.get("status") in (None, "error", "crashed")
    ]  # fmt: skip
    if not runs:
        return []
    listed = await read("list_workflows", limit=100)
    workflows = cast(list[JSON], listed.get("workflows") or [])
    names = {str(w.get("id")): str(w.get("name")) for w in workflows}
    items: list[Item] = []
    for run in runs:
        workflow = str(run.get("workflowId") or "")
        name = names.get(workflow, workflow)
        items.append((
            str(run.get("id")),
            {
                "what": f"The n8n workflow \"{name}\" failed.",
                "workflow": name, "workflow_id": workflow, "execution_id": run.get("id"),
                "started": run.get("startedAt"), "stopped": run.get("stoppedAt"),
            },
        ))  # fmt: skip
    return items


async def _vercel(read: Read, event: dict[str, Any]) -> list[Item]:
    page = await read("list_deployments", project=event["project"], limit=10)
    items: list[Item] = []
    for deploy in cast(list[JSON], page.get("deployments") or []):
        if str(deploy.get("state") or "").upper() != "ERROR":
            continue
        items.append((
            str(deploy.get("id")),
            {
                "what": f"A {deploy.get('environment') or 'preview'} deploy of {event['project']} "
                "on Vercel failed.",
                "link": f"https://{deploy['url']}" if deploy.get("url") else "",
                "deployment_id": deploy.get("id"), "environment": deploy.get("environment"),
                "commit": deploy.get("commit"), "created": deploy.get("created"),
            },
        ))  # fmt: skip
    return items


async def _github(read: Read, event: dict[str, Any]) -> list[Item]:
    repo = str(event["repo"])
    if event.get("watch") == "issues":
        found = await read("list_issues", repo=repo, state="open", limit=30)
        return [
            (
                f"issue:{i.get('number')}",
                {
                    "what": f"New issue in {repo}: #{i.get('number')} {i.get('title')}",
                    "link": i.get("url"), "number": i.get("number"), "title": i.get("title"),
                    "author": i.get("author"), "labels": i.get("labels"),
                },
            )
            for i in cast(list[JSON], found.get("issues") or [])
        ]  # fmt: skip
    args: dict[str, Any] = {"repo": repo, "limit": 30}
    if event.get("branch"):
        args["branch"] = str(event["branch"])
    found = await read("list_check_runs", **args)
    branch = found.get("branch")
    return [
        (
            f"check:{c.get('id')}",
            {
                "what": f"The check \"{c.get('name')}\" failed on {repo} "
                f"({branch}, {c.get('sha')}).",
                "link": c.get("url"), "check": c.get("name"), "conclusion": c.get("conclusion"),
                "branch": branch, "commit": c.get("sha"),
            },
        )
        for c in cast(list[JSON], found.get("check_runs") or [])
        if c.get("conclusion") in FAILED_CHECKS
    ]  # fmt: skip


async def _website(_read: Read, event: dict[str, Any]) -> list[Item]:
    """Down is one event, however long it lasts: its id is when it went down."""
    url = str(event["url"])
    state = cast(dict[str, Any], event.get("_state") or {})
    problem: str | None = None
    try:
        async with httpx.AsyncClient(
            timeout=WEBSITE_SECONDS, follow_redirects=True, max_redirects=5,
            transport=WEBSITE_TRANSPORT,
        ) as client:  # fmt: skip
            response = await client.get(url, headers={"User-Agent": "Ringkeeper website check"})
        if response.status_code >= 400:
            problem = f"it answered {response.status_code}"
    except httpx.TimeoutException:
        problem = f"no answer in {WEBSITE_SECONDS:.0f} seconds"
    except httpx.HTTPError:
        problem = "it couldn't be reached"
    if problem is None:
        event["_keep"] = {"down_since": None}
        return []
    since = str(state.get("down_since") or event.get("_now"))
    event["_keep"] = {"down_since": since}
    return [(f"down:{since}", {"what": f"{url} is down: {problem}.", "link": url, "since": since})]


POLLERS: dict[str, Poller] = {
    "airtable": _airtable, "gmail": _gmail, "n8n": _n8n, "vercel": _vercel, "github": _github,
    "website": _website,
}  # fmt: skip


# --- Fetching (no transaction open) and applying (in the scheduler's) ---


@dataclass
class Fetched:
    items: list[Item] = field(default_factory=list[Item])
    error: str | None = None
    keep: dict[str, Any] = field(default_factory=dict[str, Any])  # e.g. a website's down_since


@dataclass(frozen=True)
class Due:
    routine_id: uuid.UUID
    project: str
    event: dict[str, Any]
    state: dict[str, Any]


def due(routine: Routine, now: datetime) -> Due | None:
    """The routine's watch, if it's time for another look."""
    state = dict(routine.state or {})
    polled = state.get("polled_at")
    if polled and now - datetime.fromisoformat(str(polled)) < POLL_EVERY:
        return None
    return Due(routine.id, routine.project.name, dict(routine.event or {}), state)


def _reader(watch: Due) -> Read:
    kind = SOURCES[str(watch.event.get("source"))]
    caller = Caller(watch.project, f"{WATCH}{watch.routine_id}", None)

    async def read(tool: str, **args: Any) -> JSON:
        from ringkeeper.services import calls  # noqa: PLC0415 (calls reaches much of the app)

        if kind is None:
            raise RuleViolationError("a website check has no connection to read")
        if ADAPTERS[kind].tool(tool).kind is not ToolKind.READ:
            raise RuleViolationError("a watch only reads")
        connection = str(watch.event.get("connection") or "")
        return await calls.run_call(ToolCall(caller, kind, tool, connection, args), get_vault())

    return read


def fetch(watch: Due, now: datetime) -> Fetched:
    """One look, outside any transaction."""
    since = datetime.fromisoformat(str(watch.state["since"])) if watch.state.get("since") else now
    event = {
        **watch.event, "_since": (since - OVERLAP).isoformat(), "_state": watch.state,
        "_now": now.astimezone(UTC).isoformat(),
    }  # fmt: skip
    poller = POLLERS[str(watch.event.get("source"))]
    try:
        items = asyncio.run(
            asyncio.wait_for(poller(_reader(watch), event), timeout=TIMEOUT_SECONDS)
        )
    except Exception as error:
        return Fetched(error=str(error)[:300] or type(error).__name__)
    return Fetched(items=items, keep=cast(dict[str, Any], event.get("_keep") or {}))


def apply(
    session: Session, settings: Settings, routine: Routine, now: datetime, found: Fetched
) -> list[Any]:
    """What a look found: each new item starts one run (the first look only notes them)."""
    from ringkeeper.services import routines  # noqa: PLC0415 (it imports this module)

    source = str((routine.event or {}).get("source"))
    state = dict(routine.state or {})
    if found.error is not None:
        routine.state = {**state, "polled_at": now.isoformat(), "error": found.error}
        return []
    seen = list(state.get("seen") or [])
    first = "seen" not in state
    started: list[Any] = []
    newest: dict[str, Any] | None = None
    for ident, fields in found.items:
        if ident in seen:
            continue
        seen.append(ident)
        if not first:
            newest = fields
            started.append(
                routines.start_run(
                    session, settings, routine, "event", now=now, event=fields,
                    event_key=f"{source}:{ident}",
                )
            )  # fmt: skip
    routine.state = {
        **state, **found.keep, "polled_at": now.isoformat(),
        "since": now.astimezone(UTC).isoformat(), "seen": seen[-SEEN_KEPT:], "error": None,
    }  # fmt: skip
    if newest is not None:  # for Home's "Automation watch" line
        routine.state["last_event"] = {"what": newest.get("what"), "at": now.isoformat()}
    return started


def fetch_all(watches: list[Due], now: datetime) -> Mapping[uuid.UUID, Fetched]:
    return {watch.routine_id: fetch(watch, now) for watch in watches}
