"""A worker's environment, built from an allowlist. Nothing is inherited.

The image adds only PATH, HOME, PYTHON* and git identity variables. On top of
that a worker gets exactly these, plus its single Claude credential.
"""

import json
from typing import Any

from ringkeeper.claude_auth import ClaudeCredential

WORKSPACE = "/workspace"
STATE_DIR = "/state"

FIXED_ENV = {
    "LANG": "C.UTF-8",
    # The SDK's own state (transcripts for resume) lives on the per-task volume.
    "CLAUDE_CONFIG_DIR": f"{STATE_DIR}/claude",
    "CLAUDE_CODE_PROJECT_DIR_NAME": "workspace",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_TELEMETRY": "1",
}

TASK_ENV = "RINGKEEPER_TASK"
TOKEN_ENV = "RINGKEEPER_GATEWAY_TOKEN"  # noqa: S105 (an env var name)


def worker_env(
    credential: ClaudeCredential,
    task: dict[str, Any],
    gateway_token: str,
    anthropic_base_url: str | None = None,
) -> dict[str, str]:
    env = dict(FIXED_ENV)
    env[TASK_ENV] = json.dumps(task, separators=(",", ":"))
    env[TOKEN_ENV] = gateway_token
    env.update(credential.env())  # exactly one variable
    if anthropic_base_url:
        env["ANTHROPIC_BASE_URL"] = anthropic_base_url
    return env
