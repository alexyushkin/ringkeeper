"""Requests sent for later (a time to run at, or canceled) and snoozed approvals.
Runs on both databases.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.sqlite_baseline import restore_triggers
from ringkeeper.storage.types import UTCDateTime

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("run_at", UTCDateTime(), nullable=True))
    op.add_column("messages", sa.Column("canceled_at", UTCDateTime(), nullable=True))
    op.add_column("decisions", sa.Column("snoozed_until", UTCDateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("canceled_at")
        batch.drop_column("run_at")
    with op.batch_alter_table("decisions") as batch:
        batch.drop_column("snoozed_until")
    restore_triggers("messages", "decisions")
