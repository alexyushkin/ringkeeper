"""The one model call the router makes, behind a small protocol so tests can
use a fake. The real one runs Claude through the Agent SDK with every tool off.

A call stops after `router_timeout_seconds` (about 90): a timeout is a transient failure,
tried again once at once; after that the message waits and is routed again later, and the
conversation is told once (router.service).
"""

import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol

import anyio

from ringkeeper import claude_failures
from ringkeeper.claude_auth import BILLING_ENV_VARS, ClaudeCredential
from ringkeeper.config import Settings
from ringkeeper.errors import RingkeeperError
from ringkeeper.vault import Vault

GOOGLE_KEY_ENV = "RINGKEEPER_GOOGLE_KEY"  # services.claude_place


class RouterModelError(RingkeeperError):
    pass


class ClaudeUnavailableError(RouterModelError):
    """Claude's side failed (a limit, the login, overload, the network): the message waits
    and is routed again, instead of failing for good."""

    def __init__(self, failure: claude_failures.Failure) -> None:
        super().__init__(failure.message)
        self.failure = failure


@dataclass(frozen=True)
class Completion:
    text: str
    cost_usd: float | None = None


class RouterModel(Protocol):
    def complete(self, system: str, prompt: str, model: str) -> Completion: ...


def sdk_env(
    credential: ClaudeCredential, config_dir: str, base_url: str | None = None
) -> dict[str, str]:
    """The SDK merges our env over the inherited one, so blank every other
    billing variable: an inherited ANTHROPIC_API_KEY must not win. `base_url`:
    where model requests go, as for workers (a company proxy; tests: a fake)."""
    env: dict[str, str] = dict.fromkeys(BILLING_ENV_VARS, "")
    env.update(credential.env())
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
    env["CLAUDE_CONFIG_DIR"] = config_dir
    return env


def with_google_key(env: dict[str, str], folder: str) -> dict[str, str]:
    """Vertex wants its key as a file: here, one that lasts as long as `folder`."""
    key = env.pop(GOOGLE_KEY_ENV, None)
    if key is None:
        return env
    path = Path(folder) / "google-key.json"
    path.write_text(key, encoding="utf-8")
    path.chmod(0o600)
    return env | {"GOOGLE_APPLICATION_CREDENTIALS": str(path)}


async def within(limit: float, call: Callable[[], Awaitable[Completion]]) -> Completion:
    """`call`, stopped after `limit` seconds: then it's a SLOW failure on Claude's side."""
    try:
        with anyio.fail_after(limit):
            return await call()
    except TimeoutError:
        raise ClaudeUnavailableError(claude_failures.timed_out(limit)) from None


class AgentSdkModel:
    def __init__(self, vault: Vault, settings: Settings, sleep: Any = time.sleep) -> None:
        self._vault = vault
        self._settings = settings
        self._sleep = sleep  # tests: no real waiting

    def complete(self, system: str, prompt: str, model: str) -> Completion:
        # Resolved per call, so `ringkeeper auth login` needs no restart.
        from ringkeeper.db import session_scope  # noqa: PLC0415 (cycle)
        from ringkeeper.services import claude_place, subscription  # noqa: PLC0415 (cycle)

        subscription.refresh(self._vault, self._settings)
        with session_scope(commit=False) as session:
            credential = claude_place.credential(session, self._vault, self._settings)
            model = claude_place.model_for(claude_place.chosen(session), model) or model
        limit = self._settings.router_timeout_seconds
        return self.with_retries(
            lambda: anyio.run(
                within, limit, partial(self._complete, credential, system, prompt, model)
            )
        )

    def with_retries(self, call: Any) -> Completion:
        """A transient failure (overloaded, rate limited, no network) is tried again a few
        times, waiting longer each time; anything else on Claude's side is raised at once.
        A call that ran out of time is tried again once, at once: one more slow one is raised,
        so the message waits and the conversation hears why."""
        slow = 0
        for wait in (*claude_failures.RETRY_WAITS, None):
            try:
                return call()
            except ClaudeUnavailableError as unavailable:
                failure = unavailable.failure
                if failure.kind is claude_failures.Kind.SLOW:
                    slow += 1
                    if slow > 1:
                        raise
                    continue
                if wait is None or not failure.transient:
                    raise
                self._sleep(wait)
        raise AssertionError("unreachable")

    def ask(self, credential: ClaudeCredential, system: str, prompt: str, model: str) -> Completion:
        """One call with this credential, whatever is saved (the live check)."""
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
            env = with_google_key(
                sdk_env(credential, scratch, self._settings.worker_anthropic_base_url), scratch
            )
            options = ClaudeAgentOptions(
                system_prompt=system,
                model=model,
                tools=[],
                max_turns=1,
                effort=self._settings.effort_router,
                setting_sources=[],
                strict_mcp_config=True,
                cwd=scratch,
                env=env,
            )
            texts: list[str] = []
            result: ResultMessage | None = None
            said_error: str | None = None
            try:
                async for message in query(prompt=prompt, options=options):
                    if isinstance(message, AssistantMessage):
                        texts.extend(b.text for b in message.content if isinstance(b, TextBlock))
                        said_error = message.error or said_error
                    elif isinstance(message, ResultMessage):
                        result = message
            except Exception as error:  # the CLI couldn't start or talk to Claude
                if failure := claude_failures.classify(str(error)):
                    raise ClaudeUnavailableError(failure) from None
                raise
        if result is None or result.is_error:
            # Claude Code's own words: its result and errors, not the model's text.
            told = [*(result.errors or []), result.result or ""] if result is not None else []
            text = "\n".join(told)
            status = result.api_error_status if result is not None else None
            if failure := claude_failures.classify(text, status=status, error=said_error):
                raise ClaudeUnavailableError(failure)
            detail = result.subtype if result is not None else "no result"
            raise RouterModelError(f"the router model call failed ({detail})")
        return Completion(result.result or "".join(texts), result.total_cost_usd)
