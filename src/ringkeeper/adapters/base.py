"""The small common interface every service adapter implements.

An adapter is one module that declares its tools with `@read` or `@write`
and exposes an `Adapter`. Each tool is a plain async function whose first
parameter is the `Target` (the resolved instance and its secret); the rest
are the tool's own arguments. The gateway adds the `instance` argument and
runs every policy check before a handler is ever called.
"""

import enum
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ringkeeper.errors import RingkeeperError
from ringkeeper.models import Env, InstanceType
from ringkeeper.vault import Secret

JSON = dict[str, Any]


class ToolKind(enum.StrEnum):
    READ = "read"
    WRITE = "write"


class Hold(enum.StrEnum):
    """When a write waits for a human."""

    PROD = "prod"  # on production connections, unless the binding preapproves it
    ALWAYS = "always"  # reaches real people or costs money: even on test data
    NEVER = "never"  # nobody else sees it (a draft): runs, and is audited


class ToolError(RingkeeperError):
    """A failure the agent should see verbatim (after scrubbing)."""


@dataclass(frozen=True)
class Target:
    name: str
    env: Env
    base_url: str | None
    secret: Secret = field(repr=False)


Handler = Callable[..., Awaitable[JSON]]
# Captures state at hold time; approval refuses to run if it has changed since.
Precondition = Callable[..., Awaitable[JSON]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: ToolKind
    description: str
    handler: Handler
    precondition: Precondition | None = None
    hold: Hold = Hold.PROD  # writes only


def _spec(
    kind: ToolKind, fn: Handler, precondition: Precondition | None, hold: Hold = Hold.PROD
) -> ToolSpec:
    description = (fn.__doc__ or "").strip()
    if not description:
        raise TypeError(f"tool {fn.__name__} needs a docstring: it is the agent-facing help")
    return ToolSpec(fn.__name__, kind, description, fn, precondition, hold)


def read(fn: Handler) -> ToolSpec:
    return _spec(ToolKind.READ, fn, None)


def write(fn: Handler) -> ToolSpec:
    return _spec(ToolKind.WRITE, fn, None)


def send(fn: Handler) -> ToolSpec:
    """A write that reaches people (a text, an email): always held for approval."""
    return _spec(ToolKind.WRITE, fn, None, Hold.ALWAYS)


def draft(fn: Handler) -> ToolSpec:
    """A write only the user sees (a saved draft): never held, still audited."""
    return _spec(ToolKind.WRITE, fn, None, Hold.NEVER)


def guarded_write(precondition: Precondition) -> Callable[[Handler], ToolSpec]:
    """A write whose held approval is refused if `precondition` changed since the hold."""

    def decorate(fn: Handler) -> ToolSpec:
        return _spec(ToolKind.WRITE, fn, precondition)

    return decorate


@dataclass(frozen=True)
class Adapter:
    service: InstanceType
    tools: Sequence[ToolSpec]
    # Pieces of a secret that can leak on their own (e.g. the password in a DSN).
    secret_fragments: Callable[[str], list[str]] = lambda _value: []

    def __post_init__(self) -> None:
        names = [t.name for t in self.tools]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.service} adapter declares a tool twice")

    def tool(self, name: str) -> ToolSpec:
        for spec in self.tools:
            if spec.name == name:
                return spec
        raise ToolError(f"the {self.service} gateway has no tool named '{name}'")
