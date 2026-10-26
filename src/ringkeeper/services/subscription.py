"""Personal subscription mode: your own Claude subscription, on your own install only.

Off unless RINGKEEPER_ALLOW_SUBSCRIPTION=1 or the hidden toggle in Settings
(#personal) turns it on; the public docs and the setup wizard offer API keys
only. The token comes from `claude setup-token` (pasted, long-lived) or from
the Claude Code login on this computer. A login's token is short-lived: it is
copied into the vault, and copied again before a task or a request is routed
whenever Claude Code has since refreshed it (Claude Code refreshes it when it
runs). Either way the vault holds it; workers get only that one variable.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, cast

from ringkeeper.claude_auth import OAUTH_PREFIX, credential_ref
from ringkeeper.config import Settings
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.models import AuthMode
from ringkeeper.vault import Vault

NOTE = (
    "Personal use only: your own Claude subscription on your own install, as Anthropic's "
    "terms for the Agent SDK allow. Use an API key for anyone else's work."
)
FROM_LOGIN = "claude-code"
FROM_SETUP_TOKEN = "setup-token"  # noqa: S105 (a name, not a token)


def allowed(settings: Settings) -> bool:
    return settings.allow_subscription or settings.mode != "desktop"


def credentials_file() -> Path:
    """Where Claude Code keeps its login on Windows and Linux (macOS uses the keychain)."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / ".credentials.json"


def login_token() -> str:
    """The Claude Code login's current access token. Raises if there's none."""
    try:
        data: Any = json.loads(credentials_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuleViolationError(
            "Claude Code isn't signed in on this computer (or keeps its login in the "
            "system keychain). Run `claude setup-token` and paste its token instead."
        ) from None
    oauth = cast(dict[str, Any], data).get("claudeAiOauth") if isinstance(data, dict) else None
    token = cast(dict[str, Any], oauth).get("accessToken") if isinstance(oauth, dict) else None
    if not isinstance(token, str) or not token.startswith(OAUTH_PREFIX):
        raise RuleViolationError("Claude Code's login has no subscription token to use.")
    expires = cast(dict[str, Any], oauth).get("expiresAt")
    if isinstance(expires, int | float) and expires / 1000 < time.time():
        raise RuleViolationError(
            "Claude Code's login has expired. Open Claude Code once to refresh it, or use "
            "`claude setup-token`."
        )
    return token


def refresh(vault: Vault, settings: Settings) -> None:
    """Before a worker or the router starts: take Claude Code's newer token, if it has one."""
    if settings.claude_auth_mode is not AuthMode.SUBSCRIPTION:
        return
    if settings.subscription_source != FROM_LOGIN:
        return
    try:
        token = login_token()
    except RuleViolationError:
        return  # the stored one is tried, and its failure says what's wrong
    ref = credential_ref(settings, AuthMode.SUBSCRIPTION)
    try:
        current = vault.resolve(ref).reveal()
    except RingkeeperError:  # nothing stored yet
        current = ""
    if token != current:
        vault.store(ref, token)
