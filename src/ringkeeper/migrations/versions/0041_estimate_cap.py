"""A task on a Claude subscription pauses at an estimated cost, the way one on an API key pauses
at its spending cap. Runs on both databases.

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks", sa.Column("estimate_cap_usd", sa.Numeric(10, 4, asdecimal=False), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("estimate_cap_usd")
