"""Shared tools: `instances.shared` and `instances.all_projects`.

A shared tool is an MCP server that holds no client's data (a browser, files
on this computer): it can be linked to any client's projects, or to all of
them, including projects added later. Every existing connection stays a
client's connection. Runs on both databases.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("shared", "all_projects"):
        op.add_column(
            "instances", sa.Column(column, sa.Boolean(), server_default=sa.false(), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("instances") as batch:
        batch.drop_column("all_projects")
        batch.drop_column("shared")
