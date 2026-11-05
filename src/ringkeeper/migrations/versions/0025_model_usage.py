"""Tokens each session used, counted by the model proxy. Runs on both databases.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.sqlite_baseline import NEW_UUID
from ringkeeper.storage.types import UTCDateTime

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    new_id = sa.text("gen_random_uuid()") if postgres else NEW_UUID
    op.create_table(
        "model_usage",
        sa.Column("id", sa.Uuid(), server_default=new_id, nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("project", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_write_tokens", sa.BigInteger(), nullable=False),
        sa.Column("created_at", UTCDateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_model_usage"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], ondelete="CASCADE", name="fk_model_usage_session"
        ),
    )
    op.create_index("ix_model_usage_session", "model_usage", ["session_id"])


def downgrade() -> None:
    op.drop_table("model_usage")
