"""The one model call the router makes, behind a small protocol so tests can
use a fake. The real one runs Claude through the Agent SDK with every tool off.
"""

import tempfile
from dataclasses import dataclass
from typing import Protocol

import anyio

from ringkeeper.claude_auth import BILLING_ENV_VARS, ClaudeCredential, resolve
from ringkeeper.config import Settings
from ringkeeper.errors import RingkeeperError
from ringkeeper.vault import Vault


class RouterModelError(RingkeeperError):
    pass


@dataclass(frozen=True)
class Completion:
    text: str
    cost_usd: float | None = None


class RouterModel(Protocol):
    def complete(self, system: str, prompt: str, model: str) -> Completion: ...


def sdk_env(credential: ClaudeCredential, config_dir: str) -> dict[str, str]:
    """The SDK merges our env over the inherited one, so blank every other
    billing variable: an inherited ANTHROPIC_API_KEY must not win."""
    env: dict[str, str] = dict.fromkeys(BILLING_ENV_VARS, "")
    env.update(credential.env())
    env["CLAUDE_CONFIG_DIR"] = config_dir
    return env


class AgentSdkModel:
    def __init__(self, vault: Vault, settings: Settings) -> None:
        self._vault = vault
        self._settings = settings

    def complete(self, system: str, prompt: str, model: str) -> Completion:
        # Resolved per call, so `ringkeeper auth login` needs no restart.
        credential = resolve(self._vault, self._settings)
        return anyio.run(self._complete, credential, system, prompt, model)

    async def _complete(
        self, credential: ClaudeCredential, system: str, prompt: str, model: str
    ) -> Completion:
        from claude_agent_sdk import (  # noqa: PLC0415 (only the api service routes)
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        with tempfile.TemporaryDirectory(prefix="ringkeeper-router-") as scratch:
            options = ClaudeAgentOptions(
                system_prompt=system,
                model=model,
                tools=[],
                max_turns=1,
                setting_sources=[],
                strict_mcp_config=True,
                cwd=scratch,
                env=sdk_env(credential, scratch),
            )
            texts: list[str] = []
            result: ResultMessage | None = None
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    texts.extend(b.text for b in message.content if isinstance(b, TextBlock))
                elif isinstance(message, ResultMessage):
                    result = message
        if result is None or result.is_error:
            detail = result.subtype if result is not None else "no result"
            raise RouterModelError(f"the router model call failed ({detail})")
        return Completion(result.result or "".join(texts), result.total_cost_usd)
