"""Tasks that don't work on the code: general work (no branch), and a note on what
happened to files in the project folder. Runs on both databases.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks", sa.Column("general", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column("tasks", sa.Column("folder_note", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("folder_note")
        batch.drop_column("general")
