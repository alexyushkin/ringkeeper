"""Why a task was stopped by a service's plan. Runs on both databases.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("plan_limit", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "plan_limit")
