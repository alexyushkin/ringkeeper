"""Personal subscription mode: your own Claude subscription, on your own install only.

Off unless RINGKEEPER_ALLOW_SUBSCRIPTION=1 or the hidden toggle in Settings
(#personal) turns it on; until then the setup wizard offers an API key only.
The token comes from `claude setup-token` (pasted, lasts a year) or from the
Claude Code CLI's login on this computer (not the desktop app's, which stays
inside the app): a file on Windows and Linux, the Keychain on a Mac.
A login's token is short-lived: it is copied into the vault,
and copied again before a task or a request is routed whenever Claude Code has
since refreshed it (Claude Code refreshes it when it runs). Either way the
vault holds it; workers get only that one variable.
"""

import json
import os
import platform
import subprocess
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
# Where Claude Code keeps its login on a Mac. The wait leaves time to answer macOS's prompt.
SECURITY_TOOL = "/usr/bin/security"
KEYCHAIN_SERVICE = "Claude Code-credentials"
KEYCHAIN_SECONDS = 60


def allowed(settings: Settings) -> bool:
    """Whether the subscription may be chosen or changed, in both modes. An install
    already in subscription mode keeps running without it."""
    return settings.allow_subscription


def credentials_file() -> Path:
    """Where the Claude Code CLI keeps its login on Windows and Linux (a Mac uses the
    Keychain, see `_from_keychain`): ~/.claude, or CLAUDE_CONFIG_DIR.
    The Claude desktop app's own Claude
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


def _from_keychain() -> str | None:
    """The login's JSON from the Mac's Keychain, or None. macOS may ask to allow this.
    It goes through stdout only: nothing here logs it, and it never reaches an argument."""
    try:
        found = subprocess.run(  # noqa: S603 (a fixed program and arguments)
            [SECURITY_TOOL, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=KEYCHAIN_SECONDS, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    return found.stdout if found.returncode == 0 and found.stdout.strip() else None


def _stored_login() -> str | None:
    """The login as Claude Code stored it: the Keychain on a Mac, then its file."""
    if platform.system() == "Darwin":
        found = _from_keychain()
        if found is not None:
            return found
    try:
        return credentials_file().read_text(encoding="utf-8")
    except OSError:
        return None


def login_token() -> str:
    """The Claude Code CLI login's current access token. Raises, saying what it found,
    if there's none it can use."""
    try:
        data: Any = json.loads(_stored_login() or "")
    except ValueError:
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
            f"Found an old Claude Code login from {_day(expires / 1000)} that has expired. "
            f"Open Claude Code to refresh it, or: {INSTEAD}"
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
