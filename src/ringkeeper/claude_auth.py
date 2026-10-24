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

_PREFIXES = {AuthMode.API_KEY: "sk-ant-api", AuthMode.SUBSCRIPTION: "sk-ant-oat"}
_LABELS = {AuthMode.API_KEY: "an Anthropic API key", AuthMode.SUBSCRIPTION: "an OAuth token"}


class ClaudeAuthError(RingkeeperError):
    pass


def credential_ref(settings: Settings, mode: AuthMode) -> str:
    if mode is AuthMode.API_KEY:
        return settings.claude_api_key_ref
    return settings.claude_oauth_token_ref


def check_format(mode: AuthMode, value: str) -> None:
    """Catch a pasted value of the wrong kind (it would bill the wrong way)."""
    if not value.startswith(_PREFIXES[mode]):
        other = AuthMode.SUBSCRIPTION if mode is AuthMode.API_KEY else AuthMode.API_KEY
        hint = f"; that looks like {_LABELS[other]}" if value.startswith(_PREFIXES[other]) else ""
        raise ClaudeAuthError(f"this is not {_LABELS[mode]} (expected {_PREFIXES[mode]}…){hint}")


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
            f"no Claude credential for {mode} mode ({error}); run `ringkeeper auth login`"
        ) from None
    check_format(mode, secret.reveal())
    return ClaudeCredential(mode, ref, secret)
