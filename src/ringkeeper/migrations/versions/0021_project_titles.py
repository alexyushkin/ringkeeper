"""A project's title: `projects.title`, its name as people read it.

A project's `name` is a slug URLs, IDs and the AI use ("geninfos-web"). The title
is what the app shows ("geninfos.com website"), and can be changed without touching
the name. The name as typed wasn't kept until now, so existing projects get one from
their slug. Runs on both databases.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("title", sa.Text(), nullable=False, server_default=""))
    bind = op.get_bind()
    for row in bind.execute(sa.text("SELECT id, name FROM projects")).all():
        bind.execute(
            sa.text("UPDATE projects SET title = :title WHERE id = :id"),
            {"title": row.name.replace("-", " ").capitalize(), "id": row.id},
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("title")
