"""What a worker is told about its project's connections before it starts.

A tool that reads takes `instance=...`, or uses the project's default connection of that
kind. With several of a kind and no default, a call without one is refused, and the worker
has to try again. So the worker gets the list up front (each connection's name, kind,
whether it's production, and which one is the default) and names one on its first call.
When two of a kind are in different environments and the request doesn't say which it means,
the worker asks instead of guessing.
"""

from ringkeeper.adapters import ADAPTERS, agent_name
from ringkeeper.models import Agent, Env, InstanceType, Project, ProjectInstance
from ringkeeper.services import bindings, instances

HEAD = (
    "YOUR PROJECT'S CONNECTIONS. When you call a connection's tools, pass its name as "
    "instance=..., starting with your first call:"
)
ASK = (
    "If the request doesn't say which connection it means, and two of the same kind are in "
    "different environments (one production, one not), use ask_user to ask which before you "
    "use either. Don't guess."
)


def _where(env: Env) -> str:
    return "production" if env is Env.PROD else f"not production ({env.value})"


def usable(project: Project, agent: Agent | None = None) -> list[ProjectInstance]:
    """The project's links a worker can use: its agent's, if it has one, and never another
    client's connection or one the project is left out of (the gateway refuses those)."""
    return [
        b
        for b in sorted(project.bindings, key=lambda b: b.instance.name)
        if b.instance_type in ADAPTERS
        and not instances.notes_only(b.instance)
        and not bindings.crosses_clients(project, b.instance)
        and not instances.excluded(b.instance, project)
        and (agent is None or b.instance.name in agent.connections)
    ]


def note(project: Project, agent: Agent | None = None) -> str | None:
    """The block added to a worker's prompt. None: the project has no connection."""
    links = usable(project, agent)
    if not links:
        return None
    lines = [HEAD]
    for link in links:
        instance = link.instance
        called = (
            f" ({instance.title})" if instance.title and instance.title != instance.name else ""
        )
        default = ", the default for its kind" if link.is_default else ""
        kind = agent_name(link.instance_type)
        lines.append(f"- {instance.name}{called}: {kind}, {_where(instance.env)}{default}")
    places: dict[InstanceType, set[Env]] = {}
    for link in links:
        places.setdefault(link.instance_type, set()).add(link.instance.env)
    if any(len(envs) > 1 for envs in places.values()):
        lines.append(ASK)
    return "\n".join(lines)
