"""Connections that read the same: a client's gets its client's name.

Two connections could have the same title ("Google · a@acme.example" for two clients' accounts
on one address), so neither the app nor a request could tell them apart. Each client's
connection among them now reads as services.instances.suggested_title would have it: "Google ·
Acme Inc · a@acme.example" for a Google account, "Database · Acme Inc" otherwise. Your own
(shared) ones keep theirs. Runs on both databases.

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-12
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHARED_OWNER = "you"  # services.instances.SHARED_OWNER


def _told_apart(row: Any, shown: str, client: str) -> str:
    raw: Any = row.config
    config: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else (raw or {})
    email = str(config.get("email") or "")
    if str(row.type) == "google" and email:
        return f"Google · {client} · {email}"
    return f"{shown} · {client}"


def upgrade() -> None:
    bind = op.get_bind()
    names = dict(bind.execute(sa.text("SELECT slug, name FROM clients")).all())
    rows = bind.execute(
        sa.text("SELECT id, name, title, type, owner, shared, config FROM instances")
    ).all()
    same: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        same[(row.title or row.name).strip().lower()].append(row)
    for group in same.values():
        if len(group) < 2:
            continue
        for row in group:
            if row.shared or row.owner == SHARED_OWNER:
                continue
            shown = (row.title or row.name).strip()
            title = _told_apart(row, shown, names.get(row.owner, row.owner))
            bind.execute(
                sa.text("UPDATE instances SET title = :title WHERE id = :id"),
                {"title": title, "id": row.id},
            )


def downgrade() -> None:
    pass  # the titles stay: they still say which connection is which
