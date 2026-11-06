"""The owner's own memory in Notion, through the Notion MCP connection: "About me" knowledge
(preferences, style, how they work) that follows them across clients.

Notion's memory is one person's, in their own workspace, and it isn't divided by client. So it
only works on a shared Notion connection the owner turned it on for, and a save is checked here:
nothing about a client or project goes in, whatever the agent was told. Client and project
knowledge belongs in the project's own memory (services.memory).

Per Notion's docs: search is notion-memory-search, reading is notion-fetch with id "memory", and
saving is notion-create-pages with parent "memory" and a Type. Editing is an ordinary page edit and
deleting an ordinary delete, so both keep their holds (adapters.mcp).
"""

import re
from collections.abc import Iterator
from typing import Any, cast
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper import sensitive
from ringkeeper.adapters.base import Target, ToolError
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Agent, Client, Instance, InstanceType, Project
from ringkeeper.services import instances, report

NOTION = "notion"
SEARCH_TOOL = "notion-memory-search"
# Profile, Preference, Work and Notes are about the owner. People is about someone else.
TYPES = ("Profile", "Preference", "Work", "Notes")
SHORTEST_NAME = 3
COMPANY_WORDS = re.compile(r"(?i)[,\s]+(llc|l\.l\.c\.|inc\.?|ltd\.?|limited|corp\.?|gmbh|co\.?)$")
OFF_HINT = (
    "Notion isn't offering its memory tools here. They need Notion AI, which is part of the "
    "Business and Enterprise plans, and a connection you signed in to, not a token. In Notion, "
    "check Settings > Notion AI > Memory: Use memories must be on, for you and for the workspace. "
    "Then refresh."
)
INSTRUCTIONS = """\
{name} also holds the owner's own Notion memory: their preferences, style and how they work, across
every client. Reach it with call_tool on {name}.
- When you start: read the guide with notion-fetch, id "notion://docs/memory", then look for what
  applies with notion-memory-search.
- When you finish: if you learned a lasting preference of the owner's, save it with
  notion-create-pages, parent "memory", with Type set to Preference, Profile, Work or Notes.
- Never put anything about a client or project there: no client or project names, and no client
  data. That's refused. It goes in this project's own memory, with `remember`.
"""


def _enabled(instance: Instance) -> bool:
    config = instance.config or {}
    return bool(
        instance.type is InstanceType.MCP and config.get("remote") == NOTION and instance.shared
        and config.get("personal_memory")
    )  # fmt: skip


def connections(session: Session) -> list[Instance]:
    """Where it can be turned on: the owner's own, so a shared Notion connection."""
    found = instances.list_instances(session, type=InstanceType.MCP)
    return [i for i in found if i.shared and (i.config or {}).get("remote") == NOTION]


def status(instance: Instance) -> dict[str, Any]:
    config = instance.config or {}
    tools = cast(dict[str, Any], config.get("annotations") or {})
    on = bool(config.get("personal_memory"))
    offered = SEARCH_TOOL in tools
    return {
        "name": instance.name, "title": instance.title or instance.name, "enabled": on,
        "offered": offered, "listed": bool(tools),
        "hint": OFF_HINT if on and tools and not offered else None,
    }  # fmt: skip


def set_enabled(session: Session, name: str, enabled: bool) -> dict[str, Any]:
    instance = instances.get_instance(session, name)
    if instance.type is not InstanceType.MCP or (instance.config or {}).get("remote") != NOTION:
        raise RuleViolationError(f"'{name}' isn't a Notion connection.")
    if enabled and not instance.shared:
        raise RuleViolationError(
            "Notion memory is one person's, across every client, so it only goes on your own "
            "shared Notion connection, not a client's."
        )
    instance.config = {**(instance.config or {}), "personal_memory": enabled}
    return status(instance)


def instructions(project: Project, agent: Agent | None) -> str | None:
    """What a worker is told, if this project reaches a connection it's on for."""
    for binding in project.bindings:
        instance = binding.instance
        if _enabled(instance) and (agent is None or instance.name in agent.connections):
            return INSTRUCTIONS.format(name=instance.name)
    return None


# --- What a save may hold ---


def _spaced(name: str) -> str:
    return re.sub(r"[-_\s]+", " ", name).strip()


def _words(session: Session) -> set[str]:
    """Every name that says this is about a client: clients, projects, and their addresses."""
    names: set[str] = set()
    for slug, name in session.execute(select(Client.slug, Client.name)):
        names |= {_spaced(slug), _spaced(name), _spaced(COMPANY_WORDS.sub("", name))}
    for project in session.scalars(select(Project)):
        names |= {_spaced(project.name), _spaced(project.title), _spaced(project.owner)}
        names |= {_spaced(alias) for alias in project.aliases}
    for instance in session.scalars(select(Instance).where(Instance.shared.is_(False))):
        host = urlsplit(instance.base_url or "").hostname
        if host:
            names.add(host.removeprefix("www."))
    return {n.lower() for n in names if len(n) >= SHORTEST_NAME}


def mentions(session: Session, text: str) -> str | None:
    """The first client or project this text names, or None. Hyphens and spaces count alike."""
    plain = _spaced(text).lower()
    for name in sorted(_words(session), key=len, reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", plain):
            return name
    return None


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for inner in cast(dict[str, Any], value).values():
            yield from _strings(inner)
    elif isinstance(value, list):
        for inner in cast(list[Any], value):
            yield from _strings(inner)


def _type_of(page: dict[str, Any]) -> str | None:
    properties = cast(dict[str, Any], page.get("properties") or {})
    found = properties.get("Type")
    return found if isinstance(found, str) else None


def check_save(session: Session, arguments: dict[str, Any]) -> None:
    pages = cast(list[Any], arguments.get("pages") or [])
    if not pages or arguments.get("creation_mode"):
        raise ToolError('save a memory with pages, parent "memory", and no creation_mode')
    for page in pages:
        if _type_of(cast(dict[str, Any], page)) not in TYPES:
            raise ToolError(
                f"a saved memory's Type is one of {', '.join(TYPES)}. It's about the owner: "
                "keep other people out of it"
            )
    text = "\n".join(_strings(pages))
    if sensitive.find(text) or report.clean(text) != text:
        raise ToolError(
            "that wasn't saved: it looks like it holds a secret or someone's personal details"
        )
    if found := mentions(session, text):
        raise ToolError(
            f"that wasn't saved: it names '{found}'. Notion memory is only for the owner's own "
            "preferences. Keep client and project knowledge in this project's memory, with "
            "`remember`, or say it without the name"
        )


def check(target: Target, kind: str, arguments: dict[str, Any]) -> None:
    """Whether this memory call may run. `kind` is from adapters.mcp.memory_call."""
    with session_scope(commit=False) as session:
        instance = instances.find_instance(session, target.name)
        if instance is None or not _enabled(instance):
            raise ToolError(
                "Notion memory isn't on for this connection. The owner turns it on for their "
                "own shared Notion connection, in Settings > Memory"
            )
        if kind == "save":
            check_save(session, arguments)
