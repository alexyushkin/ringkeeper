"""The one Claude credential a worker gets, and nothing that could switch billing.

api_key mode gives a worker ANTHROPIC_API_KEY. subscription mode (your own
install only) gives it CLAUDE_CODE_OAUTH_TOKEN and nothing else: in particular
ANTHROPIC_API_KEY must be absent, or Claude Code would silently bill the API
key instead. Workers get an environment built from an allowlist, never an
inherited one, so none of BILLING_ENV_VARS can sneak in.
"""

from dataclasses import dataclass

from ringkeeper.config import Settings
from ringkeeper.errors import RingkeeperError
from ringkeeper.models import AuthMode
from ringkeeper.vault import Secret, Vault

CREDENTIAL_ENV = {
    AuthMode.API_KEY: "ANTHROPIC_API_KEY",
    AuthMode.SUBSCRIPTION: "CLAUDE_CODE_OAUTH_TOKEN",
}

# Every variable Claude Code reads that changes who is billed or where requests
# go (per its auth precedence docs). A worker may only ever see the one from
# CREDENTIAL_ENV for its mode.
BILLING_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
)

ANTHROPIC_PREFIX = "sk-ant-"
# Subscription (OAuth) tokens are the one kind told apart by prefix. API keys
# come with more than one (sk-ant-api..., sk-ant-usr...), so whether a key
# works is Anthropic's to say (services.secrets_in checks it with a free call).
OAUTH_PREFIX = "sk-ant-oat"
NOT_AN_API_KEY = (
    "This is a Claude subscription token, not an API key. Ringkeeper needs an API key "
    "from platform.claude.com (Settings > API keys)."
)


class ClaudeAuthError(RingkeeperError):
    pass


def credential_ref(settings: Settings, mode: AuthMode) -> str:
    if mode is AuthMode.API_KEY:
        return settings.claude_api_key_ref
    return settings.claude_oauth_token_ref


def normalize(value: str) -> str:
    """Drop a BOM and every whitespace character.

    Credentials never contain whitespace, but copies of long tokens often do:
    a terminal wraps the line, or PowerShell appends a CRLF when piping.
    """
    return "".join(value.removeprefix("﻿").split())


def check_format(mode: AuthMode, value: str) -> None:
    """Catch a pasted value of the wrong kind (it would bill the wrong way).

    Only the broad shape: any sk-ant- value may be an API key, except a
    subscription token. Error messages describe the value's shape, never the
    value itself.
    """
    oauth = value.startswith(OAUTH_PREFIX)
    if mode is AuthMode.API_KEY:
        if oauth:
            raise ClaudeAuthError(NOT_AN_API_KEY)
        if not value.startswith(ANTHROPIC_PREFIX):
            raise ClaudeAuthError(
                f"This isn't an Anthropic API key: those start with {ANTHROPIC_PREFIX} "
                f"({len(value)} characters after removing whitespace)."
            )
    elif not oauth:
        found = (
            "it looks like an Anthropic API key"
            if value.startswith(ANTHROPIC_PREFIX)
            else f"it doesn't start with {ANTHROPIC_PREFIX}"
        )
        raise ClaudeAuthError(
            f"this is not an OAuth token: expected a value starting with {OAUTH_PREFIX}..., "
            f"but {found} ({len(value)} characters after removing whitespace)"
        )


@dataclass(frozen=True)
class ClaudeCredential:
    mode: AuthMode
    ref: str
    secret: Secret

    def env(self) -> dict[str, str]:
        """Exactly one variable. Callers build the rest of the env from an allowlist."""
        return {CREDENTIAL_ENV[self.mode]: self.secret.reveal()}


def resolve(vault: Vault, settings: Settings, mode: AuthMode | None = None) -> ClaudeCredential:
    mode = mode or settings.claude_auth_mode
    ref = credential_ref(settings, mode)
    try:
        secret = vault.resolve(ref)
    except RingkeeperError as error:
        raise ClaudeAuthError(
            f"no Claude credential for {mode} mode ({error}); run `ringkeeper auth login"
            f"{' --subscription' if mode is AuthMode.SUBSCRIPTION else ''}`"
        ) from None
    check_format(mode, secret.reveal())
    return ClaudeCredential(mode, ref, secret)
