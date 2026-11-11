"""A conversation's tag: "setup-check" for the ones Check my setup started, so they can be
found and archived together, and their work kept to reading. Runs on both databases.

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("tag", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("threads") as batch:
        batch.drop_column("tag")
