"""The models Settings offers: Anthropic's own list (GET /v1/models, with the stored
key and workspace), the recommended ones first with a label and a relative cost.

If the list can't be fetched (no key yet, subscription mode, offline), the
recommended ones are offered anyway; any other id can be typed in ("Custom").
"""

from dataclasses import dataclass
from typing import Any

import anthropic

from ringkeeper.claude_auth import WORKSPACE_HEADER, ClaudeAuthError, resolve
from ringkeeper.config import Settings
from ringkeeper.models import AuthMode
from ringkeeper.vault import Vault


@dataclass(frozen=True)
class Known:
    id: str
    name: str
    note: str
    cost: str  # relative: $ cheapest .. $$$ dearest


RECOMMENDED = (
    Known("claude-sonnet-5-5", "Sonnet 5.5", "Balanced: most work", "$$"),
    Known("claude-haiku-5-5", "Haiku 5.5", "Fastest and cheapest: small tasks", "$"),
    Known("claude-opus-5-5", "Opus 5.5", "Most capable: big or unclear work", "$$$"),
)


def _listed(settings: Settings, vault: Vault) -> list[dict[str, Any]]:
    credential = resolve(vault, settings, AuthMode.API_KEY)
    headers = {WORKSPACE_HEADER: credential.workspace} if credential.workspace else None
    client = anthropic.Anthropic(
        api_key=credential.secret.reveal(), max_retries=1, timeout=15.0, default_headers=headers
    )
    try:
        return [
            {"id": m.id, "name": m.display_name, "created_at": m.created_at.isoformat()}
            for m in client.models.list(limit=100)
        ]
    finally:
        client.close()


def offered(settings: Settings, vault: Vault) -> dict[str, Any]:
    error: str | None = None
    listed: list[dict[str, Any]] = []
    if settings.claude_auth_mode is AuthMode.API_KEY:
        try:
            listed = _listed(settings, vault)
        except ClaudeAuthError:
            error = "No API key is stored yet."
        except anthropic.APIError as failed:
            error = f"Anthropic's model list isn't available right now ({type(failed).__name__})."
    else:
        error = "The model list needs an API key; subscription mode uses the recommended ones."
    available = {m["id"] for m in listed}
    models = [
        {"id": k.id, "name": k.name, "note": k.note, "cost": k.cost, "recommended": True}
        for k in RECOMMENDED
        if not listed or k.id in available
    ]
    known = {k.id for k in RECOMMENDED}
    models += [
        {"id": m["id"], "name": m["name"], "note": "", "cost": "", "recommended": False}
        for m in sorted(listed, key=lambda m: m["created_at"], reverse=True)
        if m["id"] not in known
    ]
    return {"listed": bool(listed), "error": error, "models": models}
