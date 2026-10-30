"""Personal subscription mode: your own Claude subscription, on your own install only.

Off unless RINGKEEPER_ALLOW_SUBSCRIPTION=1 or the hidden toggle in Settings
(#personal) turns it on; until then the setup wizard offers an API key only.
The token comes from `claude setup-token` (pasted, lasts a year) or from the
Claude Code CLI's login on this computer (not the desktop app's, which stays
inside the app). A login's token is short-lived: it is copied into the vault,
and copied again before a task or a request is routed whenever Claude Code has
since refreshed it (Claude Code refreshes it when it runs). Either way the
vault holds it; workers get only that one variable.
"""

import json
import os
import time
from datetime import datetime
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
    """Whether the subscription may be chosen or changed, in both modes. An install
    already in subscription mode keeps running without it."""
    return settings.allow_subscription


def credentials_file() -> Path:
    """Where the Claude Code CLI keeps its login on Windows and Linux (macOS uses the
    keychain): ~/.claude, or CLAUDE_CONFIG_DIR. The Claude desktop app's own Claude
    Code signs in through the app and keeps no file here."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / ".credentials.json"


INSTEAD = (
    "Run `claude setup-token` in a terminal and paste the token it prints instead "
    "(it lasts a year)."
)


def _day(seconds: float) -> str:
    day = datetime.fromtimestamp(seconds)
    return f"{day:%B} {day.day}, {day.year}"


def login_token() -> str:
    """The Claude Code CLI login's current access token. Raises, saying what it found,
    if there's none it can use."""
    path = credentials_file()
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuleViolationError(
            "No Claude Code login found on this computer (the Claude desktop app keeps "
            f"its own, which Ringkeeper can't use). {INSTEAD}"
        ) from None
    oauth = cast(dict[str, Any], data).get("claudeAiOauth") if isinstance(data, dict) else None
    token = cast(dict[str, Any], oauth).get("accessToken") if isinstance(oauth, dict) else None
    if not isinstance(token, str) or not token.startswith(OAUTH_PREFIX):
        raise RuleViolationError(f"Claude Code's login has no subscription token. {INSTEAD}")
    expires = cast(dict[str, Any], oauth).get("expiresAt")
    if isinstance(expires, int | float) and expires / 1000 < time.time():
        raise RuleViolationError(
            f"Found an old Claude Code login from {_day(path.stat().st_mtime)} that has "
            f"expired. {INSTEAD}"
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
