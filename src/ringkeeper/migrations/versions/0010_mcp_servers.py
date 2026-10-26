"""MCP servers as connections: the `mcp` type, and a `config` for every connection.

`config` holds what isn't secret (for an MCP server: how it runs, a preview of
its command with secrets masked, and which tools are read-only); secrets stay
in the vault. Runs on both databases.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.types import Doc

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE instance_type ADD VALUE IF NOT EXISTS 'mcp'")
    op.add_column("instances", sa.Column("config", Doc, server_default="{}", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("instances") as batch:
        batch.drop_column("config")
