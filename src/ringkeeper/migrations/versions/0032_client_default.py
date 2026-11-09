"""A client's default project: where a request goes that names the client and needs no
particular project. Runs on both databases.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clients") as table:
        table.add_column(sa.Column("default_project", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("clients") as table:
        table.drop_column("default_project")
