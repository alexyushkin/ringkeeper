"""A pass can be for every client: no project of its own, the AI app chooses one at a time
(services.passes). Runs on both databases.

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.sqlite_baseline import restore_triggers

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("passes") as batch:
        batch.alter_column("project_id", existing_type=sa.Uuid(), nullable=True)
    restore_triggers("passes")


def downgrade() -> None:
    # A pass for every client has no project to go back to: it goes.
    op.execute("DELETE FROM passes WHERE project_id IS NULL")
    with op.batch_alter_table("passes") as batch:
        batch.alter_column("project_id", existing_type=sa.Uuid(), nullable=False)
    restore_triggers("passes")
