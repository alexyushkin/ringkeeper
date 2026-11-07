"""Passes: an AI app on this computer using one project through the gateway. Runs on both
databases.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.sqlite_baseline import NEW_UUID
from ringkeeper.storage.types import UTCDateTime

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    new_id = sa.text("gen_random_uuid()") if postgres else NEW_UUID
    op.create_table(
        "passes",
        sa.Column("id", sa.Uuid(), server_default=new_id, nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", UTCDateTime(), nullable=True),
        sa.Column("revoked_at", UTCDateTime(), nullable=True),
        sa.Column("revoked_by", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_passes"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE", name="fk_passes_project"
        ),
        sa.UniqueConstraint("name", name="passes_name_key"),
        sa.UniqueConstraint("token_hash", name="passes_token_hash_key"),
    )


def downgrade() -> None:
    op.drop_table("passes")
