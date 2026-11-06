"""What a service's plan refused in each session. Runs on both databases.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.types import Doc

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("plan_refused", Doc, nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "plan_refused")
