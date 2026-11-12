"""A task that couldn't do everything says what it did finish: its card shows "Partly done".
The status stays not_done. Runs on both databases.

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("partly_done", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("partly_done")
