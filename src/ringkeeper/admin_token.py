"""The API's two tokens, both random and stored in the vault.

- The admin token (`ringkeeper auth api-token`): full access, for your scripts.
- The web token: what the web app's server side holds. It only works with a
  signed-in owner's session (apart from setup and sign-in), so whoever takes
  over the internet-facing web container still can't use the API without
  the owner. `ringkeeper web reset-login` replaces it.
"""

import secrets

from ringkeeper.config import get_settings
from ringkeeper.errors import RingkeeperError
from ringkeeper.vault import Vault, get_vault

TOKEN_PREFIX = "rk_admin_"  # noqa: S105 (a prefix, so leaked tokens are easy to spot)
WEB_TOKEN_PREFIX = "rk_web_"  # noqa: S105


def generate_api_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def generate_web_token() -> str:
    return WEB_TOKEN_PREFIX + secrets.token_urlsafe(32)


def _stored(ref: str, vault: Vault | None) -> str | None:
    try:
        return (vault or get_vault(get_settings())).resolve(ref).reveal()
    except RingkeeperError:  # not stored, or no master key: callers fail closed
        return None


def stored_api_token(vault: Vault | None = None) -> str | None:
    return _stored(get_settings().api_token_ref, vault)


def stored_web_token(vault: Vault | None = None) -> str | None:
    return _stored(get_settings().web_token_ref, vault)
