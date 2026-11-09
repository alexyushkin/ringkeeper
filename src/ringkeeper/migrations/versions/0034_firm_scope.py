"""Where a request was sent (the client and project chosen at the top of the app), and
which client is the operator's own. Runs on both databases.

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("target_client", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("target_project", sa.Text(), nullable=True))
    op.add_column(
        "clients", sa.Column("own", sa.Boolean(), server_default=sa.false(), nullable=False)
    )


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("target_project")
        batch.drop_column("target_client")
    with op.batch_alter_table("clients") as batch:
        batch.drop_column("own")
