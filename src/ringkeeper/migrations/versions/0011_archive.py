"""Archiving: `archived_at` on conversations (threads) and tasks.

An archived one is hidden from the lists and still found by search. Runs on
both databases.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.types import UTCDateTime

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("threads", "tasks"):
        op.add_column(table, sa.Column("archived_at", UTCDateTime(), nullable=True))


def downgrade() -> None:
    for table in ("threads", "tasks"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("archived_at")
