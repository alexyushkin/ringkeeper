"""Agents and routines (services.agents, services.routines), and routines' webhooks.

A webhook call needs no sign-in: the routine's secret, in its address, is the
check. On the desktop it answers only on this computer (desktop.server); outside
webhooks come with the cloud hub.
"""

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import OWNER, SessionDep, WriteSessionDep, require_owner
from ringkeeper.config import get_settings
from ringkeeper.models import Agent, Routine
from ringkeeper.schemas import Input, ModelId
from ringkeeper.services import admin_audit, agents, projects, routines
from ringkeeper.services.agents import AgentIn
from ringkeeper.services.routines import RoutineIn

router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])
hooks = APIRouter()

MAX_HOOK_BYTES = 64_000

# --- Agents ---


class AgentBody(Input):
    name: Annotated[str, Field(min_length=1, max_length=63)]
    instructions: Annotated[str, Field(max_length=agents.MAX_INSTRUCTIONS)] = ""
    connections: list[Annotated[str, Field(max_length=63)]] = Field(default_factory=list[str])
    model: ModelId | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    autonomy: Literal["strict", "standard", "relaxed"] | None = None

    def data(self) -> AgentIn:
        return AgentIn(
            self.name, self.instructions, tuple(self.connections), self.model, self.effort,
            self.autonomy,
        )  # fmt: skip


class AgentOut(BaseModel):
    name: str
    instructions: str
    connections: list[str]
    model: str | None
    effort: str | None
    autonomy: str | None
    routines: int = 0
    project: str = ""
    client: str = ""


def _agent_out(agent: Agent, routine_count: int = 0) -> AgentOut:
    return AgentOut(
        name=agent.name, instructions=agent.instructions, connections=list(agent.connections),
        model=agent.model, effort=agent.effort, autonomy=agent.autonomy, routines=routine_count,
    )  # fmt: skip


@router.get("/projects/{name}/agents")
def list_agents(name: str, session: SessionDep) -> list[AgentOut]:
    counts: dict[str, int] = {}
    for routine in routines.list_routines(session, name):
        counts[routine.agent.name] = counts.get(routine.agent.name, 0) + 1
    return [_agent_out(a, counts.get(a.name, 0)) for a in agents.list_agents(session, name)]


@router.get("/agents")
def list_all_agents(
    session: SessionDep, client: str | None = None, project: str | None = None
) -> list[AgentOut]:
    """Every project's agents (the Agents page), narrowed like the switcher."""
    counts: dict[object, int] = {}
    for routine in routines.list_routines(session, project, client):
        counts[routine.agent_id] = counts.get(routine.agent_id, 0) + 1
    return [
        _agent_out(agent, counts.get(agent.id, 0)).model_copy(
            update={"project": found.name, "client": found.owner}
        )
        for agent, found in agents.list_all(session, client=client, project=project)
    ]


@router.post("/projects/{name}/agents", status_code=201)
def create_agent(name: str, data: AgentBody, session: WriteSessionDep) -> AgentOut:
    agent = agents.create_agent(session, name, data.data())
    admin_audit.record(session, "agent.create", f"added agent {agent.name}", by=OWNER, project=name)
    return _agent_out(agent)


@router.put("/projects/{name}/agents/{agent}")
def update_agent(name: str, agent: str, data: AgentBody, session: WriteSessionDep) -> AgentOut:
    changed = agents.update_agent(session, name, agent, data.data())
    admin_audit.record(
        session, "agent.update", f"changed agent {changed.name}", by=OWNER, project=name
    )
    return _agent_out(changed)


@router.delete("/projects/{name}/agents/{agent}", status_code=204)
def delete_agent(name: str, agent: str, session: WriteSessionDep) -> None:
    agents.delete_agent(session, name, agent)
    admin_audit.record(session, "agent.delete", f"removed agent {agent}", by=OWNER, project=name)


# --- Routines ---


class RoutineBody(Input):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    agent: Annotated[str, Field(min_length=1, max_length=63)]
    prompt: Annotated[str, Field(min_length=1, max_length=20_000)]
    trigger: Literal["schedule", "event", "manual"]
    schedule: dict[str, Any] | None = None
    event: dict[str, Any] | None = None
    budget_usd: Annotated[float, Field(gt=0, le=1000)] | None = None
    max_steps: Annotated[int, Field(ge=1, le=500)] = 40
    max_minutes: Annotated[int, Field(ge=1, le=240)] = 15
    max_runs_per_day: Annotated[int, Field(ge=1, le=500)] = 10
    notify: Literal["app", "channels", "none"] = "app"
    catch_up: bool = True
    tz: Annotated[str, Field(max_length=64)] | None = None  # the browser's time zone

    def data(self) -> RoutineIn:
        return RoutineIn(
            self.name, self.agent, self.prompt, self.trigger, self.schedule, self.event,
            self.budget_usd, self.max_steps, self.max_minutes, self.max_runs_per_day,
            self.notify, self.catch_up,
        )  # fmt: skip


class RoutineOut(BaseModel):
    id: str
    project: str
    client: str
    agent: str
    name: str
    prompt: str
    trigger: str
    when: str  # in words
    schedule: dict[str, Any] | None
    event: dict[str, Any] | None
    budget_usd: float | None
    max_steps: int
    max_minutes: int
    max_runs_per_day: int
    notify: str
    catch_up: bool
    paused: bool
    project_paused: bool
    next_run_at: datetime | None
    hook_path: str | None  # POST here (on the webhooks' address) to run it
    hook_url: str | None = None  # desktop: the whole address, on this computer
    last_run_at: datetime | None = None
    last_status: str | None = None  # the last run's task status, or "skipped"


def _routine_out(session: SessionDep, routine: Routine) -> RoutineOut:
    last = routines.last_runs(session, [routine.id]).get(routine.id)
    settings = get_settings()
    hook = f"/hooks/{routine.id}/{routine.hook_secret}" if routine.hook_secret else None
    status = None
    if last is not None:
        task = session.get(routines.Task, last.task_id) if last.task_id else None
        status = task.status.value if task is not None else "skipped" if last.skipped else None
    return RoutineOut(
        id=str(routine.id), project=routine.project.name, client=routine.project.owner,
        agent=routine.agent.name, name=routine.name, prompt=routine.prompt,
        trigger=routine.trigger, when=routines.describe_when(routine),
        schedule=routine.schedule, event=routine.event, budget_usd=routine.budget_usd,
        max_steps=routine.max_steps, max_minutes=routine.max_minutes,
        max_runs_per_day=routine.max_runs_per_day, notify=routine.notify,
        catch_up=routine.catch_up, paused=routine.paused,
        project_paused=routine.project.paused, next_run_at=routine.next_run_at,
        hook_path=hook,
        hook_url=f"http://127.0.0.1:{settings.hooks_port}{hook}"
        if hook and settings.mode == "desktop" and settings.hooks_port
        else None,
        last_run_at=last.created_at if last is not None else None, last_status=status,
    )  # fmt: skip


@router.get("/routine-templates")
def templates() -> dict[str, dict[str, Any]]:
    """Starting points: daily digest, new-lead follow-up draft, weekly report, data sync check."""
    return routines.TEMPLATES


@router.get("/routines")
def list_routines(
    session: SessionDep, project: str | None = None, client: str | None = None
) -> list[RoutineOut]:
    return [_routine_out(session, r) for r in routines.list_routines(session, project, client)]


@router.post("/projects/{name}/routines", status_code=201)
def create_routine(name: str, data: RoutineBody, session: WriteSessionDep) -> RoutineOut:
    """A new routine starts paused: try it with a test run, then turn it on."""
    routine = routines.create_routine(session, name, data.data(), tz=data.tz)
    admin_audit.record(
        session, "routine.create", f"added routine {routine.name} (paused)", by=OWNER,
        project=name,
    )  # fmt: skip
    return _routine_out(session, routine)


@router.get("/routines/{ident}")
def get_routine(ident: str, session: SessionDep) -> RoutineOut:
    return _routine_out(session, routines.get_routine(session, ident))


@router.put("/routines/{ident}")
def update_routine(ident: str, data: RoutineBody, session: WriteSessionDep) -> RoutineOut:
    routine = routines.update_routine(session, ident, data.data())
    admin_audit.record(
        session, "routine.update", f"changed routine {routine.name}", by=OWNER,
        project=routine.project.name,
    )  # fmt: skip
    return _routine_out(session, routine)


class PauseIn(Input):
    paused: bool


@router.post("/routines/{ident}/paused")
def pause_routine(ident: str, data: PauseIn, session: WriteSessionDep) -> RoutineOut:
    routine = routines.set_paused(session, ident, data.paused)
    admin_audit.record(
        session, "routine.pause" if data.paused else "routine.resume",
        f"{'paused' if data.paused else 'turned on'} routine {routine.name}", by=OWNER,
        project=routine.project.name,
    )  # fmt: skip
    return _routine_out(session, routine)


@router.post("/projects/{name}/routines/paused")
def pause_project_routines(name: str, data: PauseIn, session: WriteSessionDep) -> dict[str, bool]:
    project = projects.get_project(session, name)
    project.paused = data.paused
    admin_audit.record(
        session, "routine.pause" if data.paused else "routine.resume",
        f"{'paused' if data.paused else 'resumed'} {project.title}'s routines", by=OWNER,
        project=name,
    )  # fmt: skip
    return {"paused": project.paused}


@router.delete("/routines/{ident}", status_code=204)
def delete_routine(ident: str, session: WriteSessionDep) -> None:
    routine = routines.get_routine(session, ident)
    name, project = routine.name, routine.project.name
    routines.delete_routine(session, ident)
    admin_audit.record(
        session, "routine.delete", f"removed routine {name}", by=OWNER, project=project
    )


class RunIn(Input):
    test: bool = False  # a test run: runs while paused, and isn't counted toward the day


@router.post("/routines/{ident}/run")
def run_routine(ident: str, data: RunIn, session: WriteSessionDep) -> dict[str, Any]:
    """Run now (or a test run): one run, as its agent, within its limits."""
    routine = routines.get_routine(session, ident)
    started = routines.start_run(
        session, get_settings(), routine, "test" if data.test else "manual"
    )
    admin_audit.record(
        session, "routine.run", f"{'test run' if data.test else 'ran'} routine {routine.name}",
        by=OWNER, project=routine.project.name,
    )  # fmt: skip
    return {
        "run": str(started.run.id),
        "task_id": str(started.task.id) if started.task else None,
        "skipped": started.run.skipped,
    }


@router.get("/routines/{ident}/runs")
def routine_runs(ident: str, session: SessionDep, limit: int = 50) -> list[dict[str, Any]]:
    return [r.__dict__ for r in routines.history(session, ident, min(max(limit, 1), 500))]


# --- Webhooks ---


@hooks.post("/hooks/{ident}/{secret}", status_code=202)
async def webhook(
    ident: str, secret: str, request: Request, session: WriteSessionDep
) -> dict[str, Any]:
    """Run the routine once with the call's body (JSON, or text) as its event. An
    `Idempotency-Key` header makes a repeated call the same event (one run)."""
    raw = await request.body()
    if len(raw) > MAX_HOOK_BYTES:
        raise HTTPException(413, detail=f"at most {MAX_HOOK_BYTES} bytes")
    try:
        body: Any = json.loads(raw) if raw else {}
    except ValueError:
        body = raw.decode("utf-8", "replace")
    key = request.headers.get("idempotency-key") or request.headers.get("x-request-id")
    started = routines.hook(
        session, get_settings(), ident, secret, body, key[:200] if key else None
    )
    return {"run": str(started.run.id), "started": started.task is not None,
            "skipped": started.run.skipped}  # fmt: skip
