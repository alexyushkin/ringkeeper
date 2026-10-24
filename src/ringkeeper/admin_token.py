"""The API admin token: random, stored in the vault, shown by `ringkeeper auth api-token`."""

import secrets

from ringkeeper.config import get_settings
from ringkeeper.errors import RingkeeperError
from ringkeeper.vault import Vault, get_vault

TOKEN_PREFIX = "rk_admin_"  # noqa: S105 (a prefix, so leaked tokens are easy to spot)


def generate_api_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def stored_api_token(vault: Vault | None = None) -> str | None:
    settings = get_settings()
    try:
        return (vault or get_vault(settings)).resolve(settings.api_token_ref).reveal()
    except RingkeeperError:  # not stored, or no master key: callers fail closed
        return None
