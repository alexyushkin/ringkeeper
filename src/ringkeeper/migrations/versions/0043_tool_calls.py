"""A session counts its tool calls as they happen: a task's Steps, shown while it runs. Before,
Steps was the session's turns, known only once it ended; sessions from then keep that number.
Runs on both databases.

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.sqlite_baseline import restore_triggers

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions", sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0")
    )
    op.execute("UPDATE sessions SET tool_calls = COALESCE(turns, 0)")


def downgrade() -> None:
    # SQLite rebuilds the table: a trigger that reads it (a transcript line's live update says
    # its task) would stop the rename, so it goes first and comes back after.
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS ringkeeper_notify_session_event")
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("tool_calls")
    restore_triggers("sessions", "session_events")
