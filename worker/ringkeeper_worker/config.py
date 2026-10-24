import json
import os
from dataclasses import dataclass, field
from typing import Any, cast

TASK_ENV = "RINGKEEPER_TASK"
TOKEN_ENV = "RINGKEEPER_GATEWAY_TOKEN"  # noqa: S105 (an env var name)
WORKSPACE = "/workspace"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class WorkerConfig:
    task_id: str
    session_ordinal: int
    prompt: str
    max_turns: int
    branch: str
    gateway_token: str = field(repr=False)
    mcp_servers: dict[str, str] = field(default_factory=dict[str, str])  # name -> URL
    disallowed_tools: list[str] = field(default_factory=list[str])
    max_budget_usd: float | None = None
    resume: str | None = None  # SDK session id to continue
    stub: dict[str, Any] | None = None  # integration tests only: no LLM
    workspace: str = WORKSPACE


def load(env: dict[str, str] | None = None) -> WorkerConfig:
    env = dict(os.environ) if env is None else env
    raw = env.get(TASK_ENV)
    token = env.get(TOKEN_ENV)
    if not raw or not token:
        raise ConfigError(f"{TASK_ENV} and {TOKEN_ENV} must both be set")
    try:
        data = cast(dict[str, Any], json.loads(raw))
        return WorkerConfig(
            task_id=str(data["task_id"]),
            session_ordinal=int(data["session_ordinal"]),
            prompt=str(data["prompt"]),
            max_turns=int(data["max_turns"]),
            branch=str(data["branch"]),
            gateway_token=token,
            mcp_servers={str(k): str(v) for k, v in data.get("mcp_servers", {}).items()},
            disallowed_tools=[str(t) for t in data.get("disallowed_tools", [])],
            max_budget_usd=data.get("max_budget_usd"),
            resume=data.get("resume"),
            stub=data.get("stub"),
            workspace=str(data.get("workspace", WORKSPACE)),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise ConfigError(f"invalid {TASK_ENV}: {error}") from None
