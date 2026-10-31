"""Storing secrets the owner types into the web app: write-only, checked first.

Values go straight into the vault and are never returned, logged or put in
an audit row (only the fact that something was stored). Each kind is
checked before it's stored, so a typo shows up in the wizard and not on the
first task:

- an Anthropic API key: one free `models.list` call with the official SDK (with
  its workspace, if it needs one; a 4xx answer shows Anthropic's own message);
- a subscription token: its shape only (checking it would use your plan);
- a Telegram bot token: Telegram's getMe;
- a connection secret: its shape here, and the Test connection button after.
"""

from dataclasses import dataclass
from typing import Any, cast

import anthropic
from sqlalchemy.orm import Session

from ringkeeper import netpolicy
from ringkeeper.adapters import gmail, mcp, twilio
from ringkeeper.adapters.base import ToolError
from ringkeeper.claude_auth import WORKSPACE_HEADER, check_format, credential_ref, normalize
from ringkeeper.config import Settings
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.models import AuthMode, InstanceType
from ringkeeper.scrub import scrub
from ringkeeper.services import admin_audit
from ringkeeper.telegram.api import BotApi, TelegramError
from ringkeeper.vault import get_vault

CONNECTION_REF = "file://ringkeeper/connections/{name}"


class CheckUnavailableError(RingkeeperError):
    """The service that checks the value couldn't be reached; nothing was stored."""


@dataclass(frozen=True)
class Stored:
    verified: bool | None  # None: not checkable without side effects
    message: str


def _anthropic_says(error: anthropic.APIStatusError) -> str:
    """Anthropic's own explanation from an error answer (it never echoes the key)."""
    raw: object = error.body
    body = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
    detail = body.get("error")
    message = cast(dict[str, Any], detail).get("message") if isinstance(detail, dict) else None
    return scrub(str(message or error.message))[:500]


def check_anthropic_key(key: str, workspace: str | None = None) -> None:
    headers = {WORKSPACE_HEADER: workspace} if workspace else None
    client = anthropic.Anthropic(api_key=key, max_retries=1, timeout=20.0, default_headers=headers)
    try:
        client.models.list(limit=1)
    except anthropic.AuthenticationError as error:
        raise RuleViolationError(
            f"Anthropic didn't accept this key: {_anthropic_says(error)}"
        ) from None
    except (anthropic.APIConnectionError, anthropic.APITimeoutError):
        raise CheckUnavailableError(
            "Couldn't reach Anthropic to check the key. Check the internet "
            "connection and try again."
        ) from None
    except anthropic.RateLimitError:
        raise CheckUnavailableError("Anthropic is busy right now; try again in a minute.") from None
    except anthropic.APIStatusError as error:
        if error.status_code < 500:  # the key or workspace: say what Anthropic said
            raise RuleViolationError(f"Anthropic said: {_anthropic_says(error)}") from None
        raise CheckUnavailableError(
            f"Anthropic answered with an error ({error.status_code}); try again in a minute."
        ) from None
    finally:
        client.close()


def store_claude(
    session: Session, settings: Settings, mode: AuthMode, value: str, workspace: str | None = None
) -> Stored:
    """`workspace` is the key's Anthropic workspace, if it needs one (checked with it)."""
    value = normalize(value)
    check_format(mode, value)
    if mode is AuthMode.API_KEY:
        check_anthropic_key(value, workspace)
        result = Stored(True, "Anthropic accepted the key.")
    else:
        result = Stored(None, "Saved. It's checked on the first task (personal use only).")
    get_vault(settings).store(credential_ref(settings, mode), value)
    admin_audit.record(session, "secret.claude", f"stored the Claude {mode} credential", by="owner")
    return result


def store_telegram(session: Session, settings: Settings, token: str) -> str:
    """Returns the bot's @username."""
    token = "".join(token.split())
    if ":" not in token:
        raise RuleViolationError("That doesn't look like a bot token from @BotFather.")
    api = BotApi(token, settings.telegram_api_url)
    try:
        me = api.get_me()
    except TelegramError as error:
        if "Unauthorized" in str(error) or "Not Found" in str(error):
            raise RuleViolationError("Telegram didn't accept this bot token.") from None
        raise CheckUnavailableError("Couldn't reach Telegram; try again.") from None
    finally:
        api.close()
    get_vault(settings).store(settings.telegram_bot_token_ref, token)
    username = str(me.get("username") or "")
    admin_audit.record(
        session, "secret.telegram", f"stored the Telegram bot token (@{username})", by="owner"
    )
    return f"@{username}"


# Fine-grained, classic, and the OAuth/app kinds a user might paste.
GITHUB_TOKENS = ("github_pat_", "ghp_", "gho_", "ghu_", "ghs_")


def check_connection_secret(kind: InstanceType, secret: str) -> str:
    secret = secret.strip()
    if not secret:
        raise RuleViolationError("The secret is empty.")
    if kind is InstanceType.SUPABASE:  # any Postgres: Supabase, Neon, your own
        if secret.lower().startswith("psql"):
            raise RuleViolationError(
                "That's a psql command; paste the postgresql:// connection string."
            )
        if not secret.startswith(("postgres://", "postgresql://")):
            raise RuleViolationError(
                "Paste the database's connection string (it starts with postgresql://). In "
                "Neon it's under Connect; in Supabase, Connect > Session pooler."
            )
        netpolicy.check_dsn(secret)
    elif kind is InstanceType.GITHUB and not secret.startswith(GITHUB_TOKENS):
        raise RuleViolationError(
            "Paste a GitHub token: a fine-grained one starts with github_pat_, a classic one "
            "with ghp_."
        )
    elif kind is InstanceType.AIRTABLE and not secret.startswith("pat"):
        raise RuleViolationError(
            "Paste an Airtable personal access token (it starts with pat). Make one at "
            "airtable.com/create/tokens."
        )
    elif kind is InstanceType.MCP:
        try:
            mcp.server_spec(secret)
        except ToolError as error:
            raise RuleViolationError(str(error)) from None
    elif kind in (InstanceType.TWILIO, InstanceType.GMAIL):
        try:  # JSON with the parts (the app's form builds it); checked like the adapter reads it
            (twilio.credentials if kind is InstanceType.TWILIO else gmail.credentials)(secret)
        except ToolError as error:
            message = str(error)
            raise RuleViolationError(message[:1].upper() + message[1:] + ".") from None
    return secret


def forget(settings: Settings, ref: str) -> None:
    """Remove a removed connection's secret from the vault (when it's one we stored)."""
    if ref.startswith(CONNECTION_REF.split("{", 1)[0]):
        get_vault(settings).delete(ref)


def store_connection(
    session: Session, settings: Settings, name: str, kind: InstanceType, secret: str
) -> str:
    """Store a connection's secret; returns its secret_ref."""
    value = check_connection_secret(kind, secret)
    ref = CONNECTION_REF.format(name=name)
    get_vault(settings).store(ref, value)
    admin_audit.record(
        session, "secret.connection", f"stored the secret for {name}", by="owner", instance=name
    )
    return ref
