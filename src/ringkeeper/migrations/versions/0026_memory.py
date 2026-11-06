"""What agents remember between runs, and where each project keeps it. Runs on both databases.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.sqlite_baseline import NEW_UUID
from ringkeeper.storage.types import Doc, UTCDateTime

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    new_id = sa.text("gen_random_uuid()") if postgres else NEW_UUID
    op.add_column("projects", sa.Column("memory", Doc, server_default="{}", nullable=False))
    op.add_column("agents", sa.Column("memory", sa.Text(), nullable=True))
    op.create_table(
        "memory_notes",
        sa.Column("id", sa.Uuid(), server_default=new_id, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),  # none: the shared notes
        sa.Column("backend", sa.Text(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source_task", sa.Text(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("external_seen", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_memory_notes"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE", name="fk_memory_notes_project"
        ),
    )
    op.create_index("ix_memory_notes_project", "memory_notes", ["project_id", "backend"])


def downgrade() -> None:
    op.drop_index("ix_memory_notes_project", table_name="memory_notes")
    op.drop_table("memory_notes")
    op.drop_column("agents", "memory")
    op.drop_column("projects", "memory")
