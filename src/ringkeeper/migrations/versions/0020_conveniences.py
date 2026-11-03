"""Conveniences: `messages.attachments`, `shortcuts` and `trusts`.

A request can carry files (their ids in the attachments folder, copied into the
project when a task starts). A shortcut is a request saved on a project. A trust
lets one tool on one connection of one project run without asking until it
expires. Runs on both databases.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.sqlite_baseline import NEW_UUID, restore_triggers
from ringkeeper.storage.types import TextList, UTCDateTime

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    new_id = sa.text("gen_random_uuid()") if postgres else NEW_UUID
    empty = "{}" if postgres else "[]"

    def now(name: str) -> sa.Column[object]:
        return sa.Column(name, UTCDateTime(), server_default=sa.func.now(), nullable=False)

    op.add_column(
        "messages", sa.Column("attachments", TextList, server_default=empty, nullable=False)
    )
    op.create_table(
        "shortcuts",
        sa.Column("id", sa.Uuid(), server_default=new_id, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        now("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_shortcuts"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE", name="fk_shortcuts_project"
        ),
    )
    op.create_table(
        "trusts",
        sa.Column("id", sa.Uuid(), server_default=new_id, nullable=False),
        sa.Column("project", sa.Text(), nullable=False),
        sa.Column("instance", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        now("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_trusts"),
    )
    op.create_index("ix_trusts_lookup", "trusts", ["project", "instance", "tool"])


def downgrade() -> None:
    op.drop_index("ix_trusts_lookup", table_name="trusts")
    op.drop_table("trusts")
    op.drop_table("shortcuts")
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("attachments")
    restore_triggers("messages")
