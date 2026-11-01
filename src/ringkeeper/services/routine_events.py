"""Routines' events from connected services: a new Airtable record, a new Gmail message.

Each is checked every few minutes, through the project's own connection, reading
only. The first look notes what's there (it isn't an event); after that, each new
record or message starts one run of the routine (services.routines.start_run,
keyed by its id), with its fields as {{event...}}.

    {"source": "airtable", "connection": "acme-airtable", "base": "app...", "table": "Leads"}
    {"source": "gmail", "connection": "acme-info", "query": "from:@acme.example"}

Gmail is a Google account connection (Settings > Google app).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy.orm import Session

from ringkeeper.adapters import ADAPTERS
from ringkeeper.adapters.base import JSON, Target
from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import InstanceType, Project, Routine
from ringkeeper.services import google_oauth, instances
from ringkeeper.vault import get_vault

POLL_EVERY = timedelta(minutes=5)
OVERLAP = timedelta(minutes=10)  # Airtable: look a little further back each time
SEEN_KEPT = 500
TIMEOUT_SECONDS = 60.0
SOURCES = {"airtable": InstanceType.AIRTABLE, "gmail": InstanceType.GOOGLE}

# Tests swap these for fakes: (target, event) -> new items, each (id, fields).
Poller = Callable[[Target, dict[str, Any]], Awaitable[list[tuple[str, dict[str, Any]]]]]


def check(project: Project, event: dict[str, Any]) -> None:
    """The event's connection is one of the project's, of the right kind."""
    source = str(event.get("source"))
    wanted = SOURCES[source]
    name = str(event.get("connection") or "")
    linked = {b.instance.name: b.instance for b in project.bindings}
    if name not in linked or linked[name].type is not wanted:
        kind = "an Airtable" if wanted is InstanceType.AIRTABLE else "a Google account"
        raise RuleViolationError(f"Choose {kind} connection linked to {project.name}.")
    if source == "airtable" and not (event.get("base") and event.get("table")):
        raise RuleViolationError("Say which Airtable base and table to watch.")


async def _airtable(target: Target, event: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    since = str(event.get("_since"))
    formula = f"IS_AFTER(CREATED_TIME(), DATETIME_PARSE('{since}'))"
    handler = next(t for t in ADAPTERS[InstanceType.AIRTABLE].tools if t.name == "list_records")
    page: JSON = await handler.handler(
        target, base_id=event["base"], table=event["table"], filter_formula=formula, page_size=50
    )
    records = cast(list[JSON], page.get("records") or [])
    return [(str(r["id"]), cast(dict[str, Any], r.get("fields") or {})) for r in records]


async def _gmail(target: Target, event: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    query = " ".join(filter(None, [str(event.get("query") or ""), "newer_than:2d"]))
    handler = next(t for t in ADAPTERS[InstanceType.GOOGLE].tools if t.name == "search_mail")
    found: JSON = await handler.handler(target, query=query, limit=25)
    messages = cast(list[JSON], found.get("messages") or [])
    return [
        (str(m["id"]), {k: m.get(k, "") for k in ("from", "to", "subject", "date", "snippet")})
        for m in messages
    ]


POLLERS: dict[str, Poller] = {"airtable": _airtable, "gmail": _gmail}


def _target(name: str) -> tuple[Target, InstanceType]:
    from ringkeeper.db import session_scope  # noqa: PLC0415 (a short read of its own)

    with session_scope(commit=False) as session:
        instance = instances.get_instance(session, name)
        kind, env, base_url, ref = (
            instance.type,
            instance.env,
            instance.base_url,
            instance.secret_ref,
        )
        config = dict(instance.config or {})
    secret = google_oauth.connection_secret(get_vault(), kind, name, ref)
    secret.also_scrub(*ADAPTERS[kind].secret_fragments(secret.reveal()))
    return Target(name, env, base_url, secret, config), kind


def poll(session: Session, settings: Settings, routine: Routine, now: datetime) -> list[Any]:
    """New records or messages since the last look, each started as one run."""
    from ringkeeper.services import routines  # noqa: PLC0415 (it imports this module)

    event = dict(routine.event or {})
    source = str(event.get("source"))
    state = dict(routine.state or {})
    polled = state.get("polled_at")
    if polled and now - datetime.fromisoformat(str(polled)) < POLL_EVERY:
        return []
    since = datetime.fromisoformat(str(state["since"])) if state.get("since") else now
    target, _kind = _target(str(event["connection"]))
    try:
        items = asyncio.run(
            asyncio.wait_for(
                POLLERS[source](target, {**event, "_since": (since - OVERLAP).isoformat()}),
                timeout=TIMEOUT_SECONDS,
            )
        )
    except Exception as error:
        routine.state = {**state, "polled_at": now.isoformat(), "error": str(error)[:300]}
        return []
    seen = list(state.get("seen") or [])
    first = "seen" not in state
    started: list[Any] = []
    for ident, fields in items:
        if ident in seen:
            continue
        seen.append(ident)
        if not first:
            started.append(
                routines.start_run(
                    session,
                    settings,
                    routine,
                    "event",
                    now=now,
                    event=fields,
                    event_key=f"{source}:{ident}",
                )
            )
    routine.state = {
        **state, "polled_at": now.isoformat(), "since": now.astimezone(UTC).isoformat(),
        "seen": seen[-SEEN_KEPT:], "error": None,
    }  # fmt: skip
    return started
