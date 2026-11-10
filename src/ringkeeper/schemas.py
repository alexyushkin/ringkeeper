"""Pydantic models for input (CLI, registry files) and output (API, CLI show)."""

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from ringkeeper.models import SECRET_REF_SCHEMES, SLUG_PATTERN, Autonomy, Env, InstanceType

SECRET_REF_MAX_LENGTH = 1024

# Never echo the rejected value: if someone pasted a real secret, it must not
# end up in a terminal, a log or an API error.
SECRET_REF_ERROR = (
    "secret_ref must be a pointer like file:///path or aws-sm://name, not a secret value"  # noqa: S105
)

_WHITESPACE = re.compile(r"\s")


def validate_secret_ref(value: str) -> str:
    if len(value) > SECRET_REF_MAX_LENGTH or _WHITESPACE.search(value):
        raise ValueError(SECRET_REF_ERROR)
    try:
        parts = urlsplit(value)
    except ValueError:
        raise ValueError(SECRET_REF_ERROR) from None
    if parts.scheme not in SECRET_REF_SCHEMES or not value.startswith(f"{parts.scheme}://"):
        raise ValueError(SECRET_REF_ERROR)
    # Userinfo, query strings and fragments are where credentials hide.
    if "@" in value or parts.query or parts.fragment or "?" in value or "#" in value:
        raise ValueError(SECRET_REF_ERROR)
    target = (parts.netloc + parts.path).strip("/")
    if not target:
        raise ValueError(SECRET_REF_ERROR)
    return value


def _clean_list(values: list[str]) -> list[str]:
    """Strip items, drop empties and duplicates, keep order."""
    seen: dict[str, None] = {}
    for item in values:
        cleaned = item.strip()
        if cleaned:
            seen[cleaned] = None
    return list(seen)


Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN)]
NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SecretRef = Annotated[str, AfterValidator(validate_secret_ref)]
CleanList = Annotated[list[str], AfterValidator(_clean_list)]
# Per-project model overrides: a role (architect, worker) or a worker complexity tier.
ModelKey = Literal["architect", "worker", "low", "medium", "high"]
ModelId = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z0-9][a-z0-9.\-]*$")
]
# An Anthropic workspace (wrkspc_...): letters, digits, _ and - only, since it
# goes into a request header.
WorkspaceId = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_\-]{1,100}$")
]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


Input = _Input  # for request bodies defined next to their endpoints


Title = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]


class ProjectIn(_Input):
    name: Slug
    title: Title = ""  # as shown; empty: made from the name, or the one it has
    owner: NonEmpty
    description: str = ""
    aliases: CleanList = Field(default_factory=list[str])
    repo_url: str | None = None
    workspace_path: str | None = None
    autonomy: Autonomy = Autonomy.STANDARD
    web: bool | None = None  # its tasks can use the web; None: the default (web_access)
    # Shell commands run in the worker container after each worker turn.
    checks: CleanList = Field(default_factory=list[str])
    models: dict[ModelKey, ModelId] = Field(default_factory=dict[ModelKey, ModelId])


# A connection's name as people read it ("n8n · n8n.geninfos.com"); the slug
# `name` is what the AI and the URLs use.
class InstanceIn(_Input):
    name: Slug
    # As shown; None on an update keeps the one it has (empty: shown by its name).
    title: Title | None = None
    type: InstanceType
    env: Env
    secret_ref: SecretRef
    owner: NonEmpty
    base_url: str | None = None
    config: dict[str, Any] = Field(default_factory=dict[str, Any])
    shared: bool = False  # yours: a shared tool, linkable to any client's projects
    all_projects: bool = False  # a shared tool every project gets, new ones too
    # ...except these clients' projects and these projects (only with all_projects).
    except_clients: CleanList = Field(default_factory=list[str])
    except_projects: CleanList = Field(default_factory=list[str])


# An ID a link is narrowed to (a Neon project, an Airtable base, a GitHub owner/name):
# it is sent to the service in calls, so only plain ID characters (and one slash).
ScopeValue = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, pattern=r"^[A-Za-z0-9_.:\-]{1,100}(/[A-Za-z0-9_.:\-]{1,100})?$"
    ),
]


class BindingIn(_Input):
    project: Slug
    instance: Slug
    is_default: bool = False
    allowed_tools: CleanList = Field(default_factory=list[str])
    preapproved_writes: CleanList = Field(default_factory=list[str])
    # What this project's calls are narrowed to (ringkeeper.remote); empty: all of it.
    # Left out (None): an existing link keeps its scope.
    scope: dict[Slug, ScopeValue] | None = None


class _Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectOut(_Output):
    name: str
    title: str
    owner: str
    description: str
    aliases: list[str]
    repo_url: str | None
    workspace_path: str | None
    autonomy: Autonomy
    checks: list[str]
    models: dict[str, str]
    paused: bool = False  # its routines don't run on their own
    web: bool | None = None  # its tasks can use the web; None: the default


class InstanceOut(_Output):
    """Public view of an instance. Deliberately has no secret_ref."""

    name: str
    title: str | None = None
    type: InstanceType
    env: Env
    base_url: str | None
    owner: str


class BoundInstanceOut(InstanceOut):
    is_default: bool
    allowed_tools: list[str]
    preapproved_writes: list[str]
    scope: dict[str, str] = {}


class ProjectDetailOut(ProjectOut):
    instances: list[BoundInstanceOut]
    web_allowed: bool = False  # what it is now: set, or the default (services.web_access)
    web_default: bool = False  # what it is when it isn't set


class MessageIn(_Input):
    body: NonEmpty
    thread_id: str | None = None
    author: NonEmpty = "api"
    source: Literal["api", "web"] = "api"
    input: Literal["text", "voice"] = "text"  # voice: a transcript the user confirmed
    attachments: CleanList = Field(default_factory=list[str])  # ids from POST /attachments
    run_at: datetime | None = None  # sent for later: when the router takes it
    # The client and project chosen at the top of the app: where it goes, firmly.
    client: Slug | None = None
    project: Slug | None = None


class MessageAccepted(BaseModel):
    message_id: str
    thread_id: str


class MessageOut(_Output):
    id: uuid.UUID
    role: str
    author: str
    source: str
    input: str  # text, or voice (a confirmed transcript)
    body: str
    attachments: list[str] = []
    routing: dict[str, Any] | None
    routed_at: datetime | None
    route_error: str | None
    run_at: datetime | None = None  # sent for later
    canceled_at: datetime | None = None
    created_at: datetime


class ThreadOut(_Output):
    id: uuid.UUID
    title: str
    source: str
    created_at: datetime
    messages: list[MessageOut]
    archived: bool = False


class AnswerIn(_Input):
    choice: str | None = None
    text: str | None = None
    by: NonEmpty = "api"
    # A held write that deletes or drops data: the connection's name, typed.
    confirm: str | None = None


class DecisionOut(_Output):
    """Full details: only the admin-authenticated API and the CLI see these."""

    id: uuid.UUID
    kind: str
    status: str
    summary: str
    heading: str | None = None  # a held call: what it is, in a few words
    money: dict[str, str] | None = None  # a held call that spends: amount, who, which account
    trustable: bool = True  # a held call: whether "trust this for an hour" can work
    declares: str | None = None  # a held call: what approving states to the vendor
    client: str | None = None  # the project's client
    production: bool = False  # a held call on a production connection
    # An action for Approvals (a held call, a plan, more money); else a question,
    # asked in the conversation.
    approval: bool = True
    # A held call: whether it can be undone once it runs (services.undo.note).
    undo: dict[str, Any] | None = None
    options: list[str]
    project: str | None
    instance: str | None
    tool: str | None
    args: dict[str, Any] | None
    payload: dict[str, Any] | None
    answer: dict[str, Any] | None
    answered_via: str | None
    decided_by: str | None
    task_id: uuid.UUID | None
    thread_id: uuid.UUID | None
    created_at: datetime
    expires_at: datetime
    snoozed_until: datetime | None = None
    decided_at: datetime | None
