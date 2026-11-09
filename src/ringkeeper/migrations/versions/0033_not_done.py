"""A task that couldn't do what was asked ends "not done", and may offer to link the
connection it needed. Runs on both databases.

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.types import Doc

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # ADD VALUE can't run inside the migration's transaction block on older servers.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'not_done'")
    op.add_column("tasks", sa.Column("offer", Doc, nullable=True))


def downgrade() -> None:
    """Postgres can't drop a value from an enum; it's harmless to 0032's code."""
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("offer")
