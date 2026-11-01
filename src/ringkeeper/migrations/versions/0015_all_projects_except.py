"""All projects except some: `instances.except_clients` and `instances.except_projects`.

A shared tool for all projects can leave out some clients (their projects,
new ones too) and some projects. Empty for every existing connection. Runs on
both databases.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.types import TextList

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # An empty list: a Postgres array, or JSON on SQLite (see storage.types).
    empty = "{}" if op.get_bind().dialect.name == "postgresql" else "[]"
    for column in ("except_clients", "except_projects"):
        op.add_column(
            "instances", sa.Column(column, TextList, server_default=empty, nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("instances") as batch:
        batch.drop_column("except_projects")
        batch.drop_column("except_clients")
