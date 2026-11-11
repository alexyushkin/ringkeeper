"""The small common interface every service adapter implements.

An adapter is one module that declares its tools with `@read` or `@write`
and exposes an `Adapter`. Each tool is a plain async function whose first
parameter is the `Target` (the resolved instance and its secret); the rest
are the tool's own arguments. The gateway adds the `instance` argument and
runs every policy check before a handler is ever called.
"""

import enum
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from ringkeeper.errors import RingkeeperError
from ringkeeper.models import Env, InstanceType
from ringkeeper.vault import Secret

JSON = dict[str, Any]
# Where a tool's answer holds a picture to show (an Image): the gateway sends it as an image,
# and keeps it out of everything that reads or keeps text.
IMAGE = "@image"


class ToolKind(enum.StrEnum):
    READ = "read"
    WRITE = "write"


class Hold(enum.StrEnum):
    """When a write waits for a human."""

    PROD = "prod"  # on production connections, unless the binding preapproves it
    ALWAYS = "always"  # reaches real people or costs money: even on test data
    NEVER = "never"  # nobody else sees it: runs, and is audited
    # A saved draft: it reaches nobody, so it runs and is audited, unless the connection
    # holds drafts too (`holds_drafts`).
    DRAFT = "draft"


HOLD_DRAFTS = "hold_drafts"  # in a connection's config: drafts wait for approval too


def holds_drafts(config: "JSON") -> bool:
    """The owner wants this connection's drafts held for approval too."""
    return bool(config.get(HOLD_DRAFTS))


class ToolError(RingkeeperError):
    """A failure the agent should see verbatim (after scrubbing)."""


class PlanLimitError(ToolError):
    """The service's plan doesn't allow this (ringkeeper.plan_limits). `shown` is for the user;
    the agent also reads that another try won't help."""

    def __init__(self, shown: str) -> None:
        super().__init__(
            f"{shown} Don't try again or look for a way around it: tell the user, and carry on "
            "with what doesn't need it."
        )
        self.shown = shown


@dataclass(frozen=True)
class Image:
    """A picture a tool shows: a page drawn, or a picture from a file."""

    data: bytes = field(repr=False)
    mime: str
    width: int
    height: int


@dataclass(frozen=True)
class Target:
    name: str
    env: Env
    base_url: str | None
    secret: Secret = field(repr=False)
    config: JSON = field(default_factory=dict[str, Any])  # the connection's non-secret settings
    project: str | None = None  # the calling project (a push uses its folder)
    # What the project's link is narrowed to (ringkeeper.remote), for an adapter that
    # checks it itself: an n8n link limited to workflows with one tag.
    scope: JSON = field(default_factory=dict[str, Any])


Handler = Callable[..., Awaitable[JSON]]
# For a tool whose kind depends on the call (an MCP server's tools): READ or WRITE.
Classifier = Callable[["Target", JSON], "ToolKind"]
# Captures state at hold time; approval refuses to run if it has changed since.
Precondition = Callable[..., Awaitable[JSON]]


class Undoer:
    """How one kind of write is taken back (services.undo keeps what's needed).

    `before` reads what the write will change, as it is now, and only that: the
    rows it updates or deletes, the fields it sets, the workflow it replaces.
    `after` records how those are once the write ran, so a revert can tell
    whether something changed them since. `restore` puts `before` back.
    """

    def why_not(self, args: JSON) -> str | None:
        """None if this call can be undone; otherwise why not, in plain words."""
        return None

    async def before(self, target: "Target", args: JSON) -> JSON:
        raise NotImplementedError

    async def after(self, target: "Target", args: JSON, before: JSON, result: JSON) -> JSON:
        """How the changed things are now (compared later with `current`)."""
        return await self.current(target, before)

    async def current(self, target: "Target", kept: JSON) -> JSON:
        raise NotImplementedError

    async def restore(self, target: "Target", kept: JSON) -> JSON:
        raise NotImplementedError

    def describe(self, kept: JSON) -> str:
        """What reverting puts back, in a sentence."""
        raise NotImplementedError


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: ToolKind
    description: str
    handler: Handler
    precondition: Precondition | None = None
    hold: Hold = Hold.PROD  # writes only
    # Decides per call; `kind` is then the most careful answer (WRITE: an instance
    # must be named).
    classify: Classifier | None = None
    # How the write is taken back; None: it can't be (services.undo says why).
    undo: Undoer | None = None
    # Why this call needs the connection's name typed, or None if approving is enough.
    # Set by `held` and `final`, the two kinds of write a new connection type uses;
    # without it, ringkeeper.destructive decides. A call that needs the name always
    # asks: no test data, no trust, no per-tool setting lets it run by itself.
    typed: Callable[[JSON], str | None] | None = None
    # Refuses a call by raising ToolError, or answers with its arguments as they may go. Runs
    # off the event loop: it may read the database.
    screen: Callable[[Target, JSON], JSON] | None = None
    # A call that runs without asking but changes something: it's still in the audit log.
    logged: Callable[[Target, JSON], bool] | None = None
    # What the vendor calls the things this call names by ID, for its approval card.
    names: Callable[[Target, JSON], Awaitable[JSON]] | None = None
    # Puts a call's arguments where the tool reads them, before they're checked, so holds and
    # approval cards see them there too.
    tidy: Callable[[JSON], JSON] | None = None
    # The hold rule per call, for a tool whose calls differ (a vendor's operations).
    holds: Callable[[JSON], Hold] | None = None

    def hold_for(self, args: JSON) -> Hold:
        return self.holds(args) if self.holds is not None else self.hold

    # Its answer can hold a picture (IMAGE): sent as an image, never as text.
    shows: bool = False


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


def always_held(fn: Handler) -> ToolSpec:
    """A write that always waits for approval, even on test data."""
    return _spec(ToolKind.WRITE, fn, None, Hold.ALWAYS)


def draft(fn: Handler) -> ToolSpec:
    """A write nobody else sees (a test to the connection's own address): never held, still
    audited."""
    return _spec(ToolKind.WRITE, fn, None, Hold.NEVER)


def saves_draft(fn: Handler) -> ToolSpec:
    """Saves a draft: it reaches nobody, so it runs and is audited, unless the connection
    holds drafts too. Never needs the name typed."""
    return replace(_spec(ToolKind.WRITE, fn, None, Hold.DRAFT), typed=_never_typed)


def _always(_target: Target, _args: JSON) -> bool:
    return True


def shows(fn: Handler) -> ToolSpec:
    """A read that answers with a picture: it runs, and it's audited (never the picture)."""
    return replace(_spec(ToolKind.READ, fn, None), logged=_always, shows=True)


def saves(fn: Handler) -> ToolSpec:
    """A read that puts a copy of a file in the project's folder. Nothing outside this computer
    changes, so it runs without asking, and it's audited."""
    return replace(_spec(ToolKind.READ, fn, None), logged=_always)


def _never_typed(_args: JSON) -> str | None:
    return None


def held(fn: Handler) -> ToolSpec:
    """A write that always waits for approval, and needs nothing more (a draft, an edit)."""
    return replace(_spec(ToolKind.WRITE, fn, None, Hold.ALWAYS), typed=_never_typed)


def final(why: str | Callable[[JSON], str | None]) -> Callable[[Handler], ToolSpec]:
    """A write that also needs the connection's name typed, in the app itself: it sends to
    real people in bulk, publishes, posts, moves money, deletes, changes who has access or
    adds ongoing cost. `why` finishes "This change ...", or decides from the call's
    arguments (None: approving is enough for this one)."""
    typed: Callable[[JSON], str | None] = (lambda _args: why) if isinstance(why, str) else why

    def decorate(fn: Handler) -> ToolSpec:
        return replace(_spec(ToolKind.WRITE, fn, None, Hold.ALWAYS), typed=typed)

    return decorate


def guarded_write(
    precondition: Precondition, hold: Hold = Hold.PROD
) -> Callable[[Handler], ToolSpec]:
    """A write whose held approval is refused if `precondition` changed since the hold."""

    def decorate(fn: Handler) -> ToolSpec:
        return _spec(ToolKind.WRITE, fn, precondition, hold)

    return decorate


@dataclass(frozen=True)
class Adapter:
    service: InstanceType
    tools: Sequence[ToolSpec]
    # Pieces of a secret that can leak on their own (e.g. the password in a DSN).
    secret_fragments: Callable[[str], list[str]] = lambda _value: []
    # Whether a connection with these settings can use a tool at all (a Google account given
    # no YouTube access can't use YouTube's): the ones it can't aren't listed for it.
    offers: Callable[[JSON, str], bool] | None = None

    def __post_init__(self) -> None:
        names = [t.name for t in self.tools]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.service} adapter declares a tool twice")

    def tool(self, name: str) -> ToolSpec:
        for spec in self.tools:
            if spec.name == name:
                return spec
        raise ToolError(f"the {self.service} gateway has no tool named '{name}'")
