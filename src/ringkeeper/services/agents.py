"""A project's agents: named helpers with their own instructions, a subset of the
project's connections, and their own model, effort and autonomy.

An agent's connections are some of the ones its project is linked to; a shared
tool counts only when it's ticked for the agent. A task run as an agent gets the
agent's instructions ahead of its prompt and only those connections: the
gateway refuses the rest (services.calls), and the agent's list of connections
shows only them. Routines run an agent; it can also be named in a conversation
("ask the Seaside follow-up agent to...").
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.errors import AlreadyExistsError, NotFoundError, RuleViolationError
from ringkeeper.models import Agent, Autonomy, Project, Task, WorkerSession
from ringkeeper.names import slug
from ringkeeper.services import projects

EFFORTS = ("low", "medium", "high", "xhigh", "max")
MAX_INSTRUCTIONS = 20_000


@dataclass(frozen=True)
class AgentIn:
    name: str
    instructions: str = ""
    connections: tuple[str, ...] = ()
    model: str | None = None
    effort: str | None = None
    autonomy: str | None = None


def _check(project: Project, data: AgentIn) -> AgentIn:
    name = slug(data.name)
    if not name:
        raise RuleViolationError("Name the agent (letters or digits), like follow-up.")
    if len(data.instructions) > MAX_INSTRUCTIONS:
        raise RuleViolationError(f"Instructions are at most {MAX_INSTRUCTIONS} characters.")
    linked = {b.instance.name for b in project.bindings}
    unknown = sorted(set(data.connections) - linked)
    if unknown:
        raise RuleViolationError(
            f"{', '.join(unknown)} isn't linked to {project.name}: link it to the project "
            "first, then give it to the agent."
        )
    if data.effort is not None and data.effort not in EFFORTS:
        raise RuleViolationError(f"Effort is one of {', '.join(EFFORTS)}.")
    if data.autonomy is not None and data.autonomy not in {a.value for a in Autonomy}:
        raise RuleViolationError(f"Autonomy is one of {', '.join(a.value for a in Autonomy)}.")
    return AgentIn(
        name, data.instructions.strip(), tuple(dict.fromkeys(data.connections)),
        data.model or None, data.effort, data.autonomy,
    )  # fmt: skip


def list_agents(session: Session, project: str) -> list[Agent]:
    found = projects.get_project(session, project)
    return list(
        session.scalars(select(Agent).where(Agent.project_id == found.id).order_by(Agent.name))
    )


def get_agent(session: Session, project: str, name: str) -> Agent:
    found = projects.get_project(session, project)
    agent = session.scalar(
        select(Agent).where(Agent.project_id == found.id, Agent.name == slug(name))
    )
    if agent is None:
        raise NotFoundError(f"{project} has no agent '{name}'")
    return agent


def create_agent(session: Session, project: str, data: AgentIn) -> Agent:
    found = projects.get_project(session, project)
    data = _check(found, data)
    if session.scalar(select(Agent).where(Agent.project_id == found.id, Agent.name == data.name)):
        raise AlreadyExistsError(f"{project} already has an agent named '{data.name}'")
    agent = Agent(
        project_id=found.id, name=data.name, instructions=data.instructions,
        connections=list(data.connections), model=data.model, effort=data.effort,
        autonomy=data.autonomy,
    )  # fmt: skip
    session.add(agent)
    session.flush()
    return agent


def update_agent(session: Session, project: str, name: str, data: AgentIn) -> Agent:
    agent = get_agent(session, project, name)
    data = _check(agent.project, data)
    if data.name != agent.name and session.scalar(
        select(Agent).where(Agent.project_id == agent.project_id, Agent.name == data.name)
    ):
        raise AlreadyExistsError(f"{project} already has an agent named '{data.name}'")
    agent.name, agent.instructions = data.name, data.instructions
    agent.connections, agent.model = list(data.connections), data.model
    agent.effort, agent.autonomy = data.effort, data.autonomy
    session.flush()
    return agent


def delete_agent(session: Session, project: str, name: str) -> None:
    """Its routines go with it; tasks it ran keep their history."""
    agent = get_agent(session, project, name)
    session.delete(agent)
    session.flush()


def of_task(session: Session, task: Task) -> Agent | None:
    return session.get(Agent, task.agent_id) if task.agent_id else None


def for_worker_session(session: Session, session_id: str | None) -> Agent | None:
    """The agent a worker session's task runs as, if any (the gateway's check)."""
    try:
        ident = uuid.UUID(str(session_id))
    except ValueError:
        return None
    row = session.get(WorkerSession, ident)
    return of_task(session, row.task) if row is not None else None


def instructions_for(agent: Agent, prompt: str) -> str:
    """The prompt a worker gets when it runs as `agent`."""
    if not agent.instructions:
        return prompt
    return (
        f"You are the {agent.name} agent for this project. Your standing instructions:\n"
        f"{agent.instructions}\n\nThe task:\n{prompt}"
    )
