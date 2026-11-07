"""A worker's environment, built from an allowlist. Nothing is inherited.

The image adds only PATH, HOME, PYTHON* and git identity variables. On top of
that a worker gets exactly these, plus where the model proxy is and its own
task's token for it. No model credential: the proxy attaches that.
"""

import json
from typing import Any

WORKSPACE = "/workspace"
STATE_DIR = "/state"
# Claude Code, fetched by the first task and kept for the next (worker/.../claude_code.py).
CLI_CACHE_DIR = "/opt/claude-code"
CLI_CACHE_VOLUME = "ringkeeper-claude-code"

FIXED_ENV = {
    "LANG": "C.UTF-8",
    # The SDK's own state (transcripts for resume) lives on the per-task volume.
    "CLAUDE_CONFIG_DIR": f"{STATE_DIR}/claude",
    "CLAUDE_CODE_PROJECT_DIR_NAME": "workspace",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_TELEMETRY": "1",
    # A tool's answer can be a whole email (adapters.paging). Claude Code's own limit is lower.
    "MAX_MCP_OUTPUT_TOKENS": "100000",
}

TASK_ENV = "RINGKEEPER_TASK"
TOKEN_ENV = "RINGKEEPER_GATEWAY_TOKEN"  # noqa: S105 (an env var name)


def worker_env(model: dict[str, str], task: dict[str, Any], gateway_token: str) -> dict[str, str]:
    env = dict(FIXED_ENV)
    env[TASK_ENV] = json.dumps(task, separators=(",", ":"))
    env[TOKEN_ENV] = gateway_token
    env.update(model)  # services.claude_place.worker_vars
    return env
