"""Pydantic models for input (CLI, registry files) and output (API, CLI show)."""

import re
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from interlock.models import SECRET_REF_SCHEMES, SLUG_PATTERN, Autonomy, Env, InstanceType

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


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class ProjectIn(_Input):
    name: Slug
    owner: NonEmpty
    description: str = ""
    aliases: CleanList = Field(default_factory=list[str])
    repo_url: str | None = None
    worktree_path: str | None = None
    autonomy: Autonomy = Autonomy.STANDARD


class InstanceIn(_Input):
    name: Slug
    type: InstanceType
    env: Env
    secret_ref: SecretRef
    owner: NonEmpty
    base_url: str | None = None


class BindingIn(_Input):
    project: Slug
    instance: Slug
    is_default: bool = False
    allowed_tools: CleanList = Field(default_factory=list[str])
    preapproved_writes: CleanList = Field(default_factory=list[str])


class _Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectOut(_Output):
    name: str
    owner: str
    description: str
    aliases: list[str]
    repo_url: str | None
    worktree_path: str | None
    autonomy: Autonomy


class InstanceOut(_Output):
    """Public view of an instance. Deliberately has no secret_ref."""

    name: str
    type: InstanceType
    env: Env
    base_url: str | None
    owner: str


class BoundInstanceOut(InstanceOut):
    is_default: bool
    allowed_tools: list[str]
    preapproved_writes: list[str]


class ProjectDetailOut(ProjectOut):
    instances: list[BoundInstanceOut]
