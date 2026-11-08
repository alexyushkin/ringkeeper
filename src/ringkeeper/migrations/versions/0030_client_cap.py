"""A client's monthly cap on AI cost. Runs on both databases.

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clients") as table:
        table.add_column(
            sa.Column("monthly_cap_usd", sa.Numeric(10, 2, asdecimal=False), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("clients") as table:
        table.drop_column("monthly_cap_usd")
