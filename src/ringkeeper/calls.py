"""What a tool call is, independent of MCP: who asked, for what, with which args."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ringkeeper.models import InstanceType
from ringkeeper.scrub import scrub_obj


@dataclass(frozen=True)
class Caller:
    """Taken only from a verified worker token (all None if the token failed)."""

    project: str | None
    session_id: str | None
    token_id: str | None


@dataclass(frozen=True)
class ToolCall:
    caller: Caller
    service: InstanceType
    tool: str
    instance: str | None
    args: dict[str, Any] = field(default_factory=dict[str, Any])


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def args_hash(args: dict[str, Any]) -> str:
    """sha256 over the *redacted* args, so the hash can't be used to guess a secret."""
    return hashlib.sha256(canonical_json(scrub_obj(args)).encode()).hexdigest()
