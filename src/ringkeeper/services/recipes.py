"""Recipes: an agent and its routines as one file, to share and to start from.

A recipe is data only. It has an agent's instructions and settings and its routines' prompts
and timing; it never has a key, a connection's name, or anything that points at one client:
which connection to use, and what an event watches (a repository, a website, a base), are
chosen when it's added. It runs no code: there's nothing in the format that could, and a file
with anything else in it is refused. Added routines start paused, like any new routine.

The format is versioned (`ringkeeper_recipe`); a later Ringkeeper reads older versions.
"""

import json
from importlib import resources
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from ringkeeper import netpolicy
from ringkeeper.adapters.vendors import ALL as VENDORS
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Agent, Instance, InstanceType, Project, Routine
from ringkeeper.names import slug
from ringkeeper.scrub import scrub
from ringkeeper.services import agents, bindings, projects, routine_events, routines

VERSION = 1
MAX_BYTES = 256 * 1024
TIMEOUT = 10.0
transport: httpx2.BaseTransport | None = None  # tests answer for the link

# What an event routine needs filled in when it's added, by source (never exported).
EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "airtable": ("connection", "base", "table"),
    "gmail": ("connection",),
    "n8n": ("connection",),
    "vercel": ("connection", "project"),
    "github": ("connection", "repo"),
    "website": ("url",),
    "webhook": (),
    "folder": (),
}
KEPT_EVENT = ("source", "watch")  # what an exported event keeps: no names, no addresses
# A kind of connection: a type, or one business system's (vendor:pagespeed), since every
# business system shares the type vendor.
KINDS = {kind.value for kind in InstanceType} | {f"vendor:{v.key}" for v in VENDORS}

Text = Annotated[str, Field(max_length=20_000)]
Line = Annotated[str, Field(min_length=1, max_length=120)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecipeAgent(_Strict):
    name: Line
    instructions: Text = ""
    needs: Annotated[list[str], Field(max_length=12)] = []
    model: Annotated[str, Field(max_length=80)] | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    autonomy: Literal["strict", "standard", "relaxed"] | None = None
    memory: Literal["local", "notion"] | None = None

    @field_validator("needs")
    @classmethod
    def _kinds(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - KINDS)
        if unknown:
            raise ValueError(f"{', '.join(unknown)} isn't a kind of connection Ringkeeper knows")
        return sorted(set(value))


class RecipeEvent(_Strict):
    source: str
    watch: Literal["checks", "issues"] | None = None
    # Gmail only: which mail starts a run, in Gmail's search words (like "from:me"). A
    # recipe's own; an exported one never has it, since a search can name a client.
    query: Annotated[str, Field(min_length=1, max_length=300)] | None = None

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        if value not in EVENT_FIELDS:
            raise ValueError(f"an event is one of {', '.join(EVENT_FIELDS)}")
        return value

    @model_validator(mode="after")
    def _query(self) -> "RecipeEvent":
        if self.query is not None and self.source != "gmail":
            raise ValueError("only a Gmail event has a search")
        return self


class RecipeRoutine(_Strict):
    name: Line
    prompt: Annotated[str, Field(min_length=1, max_length=20_000)]
    trigger: Literal["schedule", "event", "manual"]
    schedule: dict[str, Any] | None = None
    event: RecipeEvent | None = None
    budget_usd: Annotated[float, Field(gt=0, le=1000)] | None = None
    max_steps: Annotated[int, Field(ge=1, le=500)] = 40
    max_minutes: Annotated[int, Field(ge=1, le=240)] = 15
    max_runs_per_day: Annotated[int, Field(ge=1, le=500)] = 10
    notify: Literal["app", "channels", "none"] = "app"
    catch_up: bool = True


class Recipe(_Strict):
    ringkeeper_recipe: Literal[1]
    name: Line
    description: Annotated[str, Field(max_length=2000)] = ""
    agent: RecipeAgent
    routines: Annotated[list[RecipeRoutine], Field(max_length=20)] = []


# --- Export ---


def kind(instance: Instance) -> str:
    """Its kind, as a recipe names it: a business system's own, else its type."""
    vendor = instance.config.get("vendor") if instance.type is InstanceType.VENDOR else None
    return f"vendor:{vendor}" if vendor else instance.type.value


def _kind_of(project: Project, name: str) -> str | None:
    found = next((b.instance for b in project.bindings if b.instance.name == name), None)
    return kind(found) if found is not None else None


def export(session: Session, project: str, agent_name: str, description: str = "") -> Recipe:
    """The agent and its routines, with nothing that names a client or holds a key."""
    agent = agents.get_agent(session, project, agent_name)
    owner = projects.get_project(session, project)
    needs = sorted({k for name in agent.connections if (k := _kind_of(owner, name))})
    theirs = [r for r in routines.list_routines(session, project) if r.agent_id == agent.id]
    return Recipe(
        ringkeeper_recipe=VERSION, name=scrub(agent.name), description=scrub(description),
        agent=RecipeAgent(
            name=agent.name, instructions=scrub(agent.instructions), needs=needs,
            model=agent.model, effort=agent.effort, autonomy=agent.autonomy, memory=agent.memory,  # type: ignore[arg-type]
        ),
        routines=[_routine(r) for r in theirs],
    )  # fmt: skip


def _routine(routine: Routine) -> RecipeRoutine:
    schedule = {k: v for k, v in (routine.schedule or {}).items() if k != "tz"} or None
    event = None
    if routine.trigger == "event" and routine.event:
        event = RecipeEvent(**{k: v for k, v in routine.event.items() if k in KEPT_EVENT})
    return RecipeRoutine(
        name=routine.name, prompt=scrub(routine.prompt),
        trigger=routine.trigger,  # type: ignore[arg-type]
        schedule=schedule if routine.trigger == "schedule" else None, event=event,
        budget_usd=routine.budget_usd, max_steps=routine.max_steps,
        max_minutes=routine.max_minutes, max_runs_per_day=routine.max_runs_per_day,
        notify=routine.notify,  # type: ignore[arg-type]
        catch_up=routine.catch_up,
    )  # fmt: skip


def as_text(recipe: Recipe) -> str:
    return json.dumps(recipe.model_dump(exclude_none=True), indent=2, ensure_ascii=False) + "\n"


def file_name(recipe: Recipe) -> str:
    return f"{slug(recipe.name) or 'recipe'}.ringkeeper-recipe.json"


# --- Reading one ---


def parse(text: str) -> Recipe:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise RuleViolationError("That recipe is over 256 KB, too big to be one.")
    try:
        data = json.loads(text)
    except ValueError:
        raise RuleViolationError("That isn't a recipe: it isn't JSON.") from None
    return parse_data(data)


def parse_data(data: object) -> Recipe:
    """A recipe as read from JSON, checked: anything that isn't in the format is refused."""
    if not isinstance(data, dict) or "ringkeeper_recipe" not in data:
        raise RuleViolationError("That isn't a Ringkeeper recipe.")
    version = cast(dict[str, Any], data).get("ringkeeper_recipe")
    if isinstance(version, int) and version > VERSION:
        raise RuleViolationError(
            "That recipe is for a newer Ringkeeper. Update Ringkeeper, then add it."
        )
    try:
        return Recipe.model_validate(data)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise RuleViolationError(f"That recipe can't be used: {where}: {first['msg']}.") from None


def direct(url: str) -> str:
    """A GitHub page for a file, as the file itself."""
    parts = urlsplit(url)
    if parts.hostname == "github.com" and "/blob/" in parts.path:
        owner_repo, _, rest = parts.path.lstrip("/").partition("/blob/")
        return urlunsplit(("https", "raw.githubusercontent.com", f"/{owner_repo}/{rest}", "", ""))
    return url


def fetch(url: str) -> Recipe:
    """A recipe from an https link. Nothing but the file is fetched, and nothing in it runs."""
    url = netpolicy.check_url(direct(url.strip()))
    if urlsplit(url).scheme != "https":
        raise RuleViolationError("A recipe's link starts with https://.")
    try:
        with (
            httpx2.Client(timeout=TIMEOUT, transport=transport, follow_redirects=False) as http,
            http.stream("GET", url, headers={"Accept": "application/json"}) as response,
        ):
            if response.is_redirect:
                raise RuleViolationError("That link redirects. Use the file's own address.")
            if response.status_code != 200:
                raise RuleViolationError(
                    f"That link answered {response.status_code}. Check it opens the file."
                )
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise RuleViolationError("That recipe is over 256 KB, too big to be one.")
    except httpx2.HTTPError:
        raise RuleViolationError("Couldn't fetch that link. Check the address.") from None
    return parse(body.decode("utf-8", errors="replace"))


# --- Starters, bundled ---


def starters() -> dict[str, Recipe]:
    folder = resources.files("ringkeeper") / "recipes"
    found: dict[str, Recipe] = {}
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".json"):
            found[entry.name.removesuffix(".json")] = parse(entry.read_text("utf-8"))
    return found


# --- Adding one to a project ---


def needs(session: Session, project: str, recipe: Recipe) -> dict[str, Any]:
    """What adding it asks for: a connection for each kind the agent uses (the project's
    linked ones of that kind, which may be none), and each event routine's details."""
    owner = projects.get_project(session, project)
    usable = [b.instance for b in owner.bindings if not bindings.crosses_clients(owner, b.instance)]
    kinds = set(recipe.agent.needs)
    watched: set[str] = set()  # an event routine can't be added without one
    for routine in recipe.routines:
        wanted = routine_events.SOURCES.get(routine.event.source) if routine.event else None
        if wanted is not None:
            watched.add(wanted.value)
    kinds |= watched
    return {
        "required": sorted(watched),
        "connections": {
            wanted: [i.name for i in usable if kind(i) == wanted] for wanted in sorted(kinds)
        },
        "events": [
            list(EVENT_FIELDS[r.event.source]) if r.trigger == "event" and r.event else []
            for r in recipe.routines
        ],
    }


def _memory(project: Project, wanted: str | None) -> str | None:
    """Notion memory only where the project has its Notion page; else as the project does."""
    if wanted == "notion" and not (project.memory or {}).get("notion"):
        return None
    return wanted


def _free_name(session: Session, project: str, name: str) -> str:
    taken = {a.name for a in agents.list_agents(session, project)}
    base = slug(name) or "agent"
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def add(
    session: Session,
    project: str,
    recipe: Recipe,
    *,
    connections: dict[str, str],
    events: list[dict[str, Any]],
    tz: str | None = None,
) -> tuple[Agent, list[Routine]]:
    """The agent and its routines in the project. Routines start paused. `connections`: the
    project's connection to use for each kind; `events`: each event routine's details."""
    owner = projects.get_project(session, project)
    linked = {b.instance.name: b.instance for b in owner.bindings}
    chosen: list[str] = []
    for wanted in recipe.agent.needs:
        name = connections.get(wanted)
        if name is None:
            continue  # added without it; the agent's page can link one later
        if name not in linked or kind(linked[name]) != wanted:
            raise RuleViolationError(f"{name} isn't a {wanted} connection linked to {project}.")
        chosen.append(name)
    agent = agents.create_agent(
        session, project,
        agents.AgentIn(
            name=_free_name(session, project, recipe.agent.name),
            instructions=recipe.agent.instructions, connections=tuple(chosen),
            model=recipe.agent.model, effort=recipe.agent.effort,
            autonomy=recipe.agent.autonomy, memory=_memory(owner, recipe.agent.memory),
        ),
    )  # fmt: skip
    made: list[Routine] = []
    for index, routine in enumerate(recipe.routines):
        event = None
        if routine.trigger == "event" and routine.event:
            filled = dict(events[index]) if index < len(events) else {}
            source = routine.event.source
            wanted = routine_events.SOURCES.get(source)
            if wanted is not None and "connection" not in filled and connections.get(wanted.value):
                filled["connection"] = connections[wanted.value]
            event = {
                **routine.event.model_dump(exclude_none=True),
                **{k: str(v).strip() for k, v in filled.items() if k in EVENT_FIELDS[source]},
            }
        data = routines.RoutineIn(
            name=routine.name, agent=agent.name, prompt=routine.prompt, trigger=routine.trigger,
            schedule=routine.schedule, event=event, budget_usd=routine.budget_usd,
            max_steps=routine.max_steps, max_minutes=routine.max_minutes,
            max_runs_per_day=routine.max_runs_per_day, notify=routine.notify,
            catch_up=routine.catch_up,
        )  # fmt: skip
        made.append(routines.create_routine(session, project, data, tz=tz))
    return agent, made
