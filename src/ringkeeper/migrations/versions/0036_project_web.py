"""Web access as a project setting of its own, apart from autonomy. Empty: the default (on for
the operator's own client's projects, off for a client's). A project that had web access
through its autonomy keeps it. Runs on both databases.

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("web", sa.Boolean(), nullable=True))
    op.execute("UPDATE projects SET web = true WHERE autonomy = 'relaxed'")


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("web")
