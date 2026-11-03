"""A connection's title: `instances.title`, its name as people read it.

A connection's `name` is a slug the AI and URLs use, made from what was typed
or from the client and service ("general-informatics-llc-a-y-yushkin"). The
title is what the app shows ("Google · a.y.yushkin@gmail.com"), and can be
changed without touching the name. Existing connections get one from their
service and account, host or client; a tool imported from Claude keeps showing
its name, which is already what it was called. Runs on both databases.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-03
"""

import json
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SERVICES = {
    "supabase": "Database", "n8n": "n8n", "airtable": "Airtable", "vercel": "Vercel",
    "twilio": "Twilio", "github": "GitHub", "gmail": "Gmail", "google": "Google",
}  # fmt: skip


def _title(kind: str, base_url: str | None, config: dict[str, Any], client: str) -> str | None:
    remote = config.get("remote")
    service = SERVICES.get(kind) or (str(remote).capitalize() if remote else None)
    if service is None:
        return None
    host = urlsplit(base_url).hostname if base_url else None
    return f"{service} · {config.get('email') or host or client}"


def upgrade() -> None:
    op.add_column("instances", sa.Column("title", sa.Text(), nullable=True))
    bind = op.get_bind()
    names = dict(bind.execute(sa.text("SELECT slug, name FROM clients")).all())
    rows = bind.execute(
        sa.text("SELECT id, type, owner, base_url, config, shared FROM instances")
    ).all()
    for row in rows:
        raw: Any = row.config
        config: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else (raw or {})
        client = "yours" if row.shared else names.get(row.owner, row.owner)
        title = _title(str(row.type), row.base_url, config, client)
        if title:
            bind.execute(
                sa.text("UPDATE instances SET title = :title WHERE id = :id"),
                {"title": title, "id": row.id},
            )


def downgrade() -> None:
    with op.batch_alter_table("instances") as batch:
        batch.drop_column("title")
