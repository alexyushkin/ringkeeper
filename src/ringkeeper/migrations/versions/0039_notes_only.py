"""A Notion memory connection holds agents' notes and is never a project's tool: its links to
projects go, and it isn't for all projects any more. Where each project's notes are kept is
unchanged. Runs on both databases.

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-12
"""

from collections.abc import Sequence
from typing import Any, cast

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.types import Doc, TextList

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOTES_ONLY = ("notion_memory",)

instances = sa.table(
    "instances", sa.column("id"), sa.column("type"), sa.column("config", Doc),
    sa.column("all_projects", sa.Boolean), sa.column("except_clients", TextList),
    sa.column("except_projects", TextList),
)  # fmt: skip
links = sa.table("project_instances", sa.column("instance_id"))


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.select(instances.c.id, instances.c.type, instances.c.config)).all()
    for ident, kind, config in rows:
        settings = cast(dict[str, Any], config or {})
        if str(kind).lower().endswith("vendor") and settings.get("vendor") in NOTES_ONLY:
            bind.execute(sa.delete(links).where(links.c.instance_id == ident))
            bind.execute(
                sa.update(instances).where(instances.c.id == ident)
                .values(all_projects=False, except_clients=[], except_projects=[])
            )  # fmt: skip


def downgrade() -> None:
    """The links aren't put back: 0038's code works without them."""
