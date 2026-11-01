"""Agents and routines: `agents`, `routines`, `routine_runs`, `projects.paused` and
`tasks.agent_id`.

An agent belongs to a project: its instructions, the connections it may use (some
of the project's), and its model, effort and autonomy. A routine runs one agent
on a schedule, an event or by hand, and records each run. Runs on both databases.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.sqlite_baseline import NEW_UUID, restore_triggers
from ringkeeper.storage.types import Doc, TextList, UTCDateTime

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    new_id = sa.text("gen_random_uuid()") if postgres else NEW_UUID
    empty_list = "{}" if postgres else "[]"
    empty_doc = "{}"
    false = sa.text("false") if postgres else sa.text("0")
    true = sa.text("true") if postgres else sa.text("1")

    def now(name: str) -> sa.Column[object]:
        return sa.Column(name, UTCDateTime(), server_default=sa.func.now(), nullable=False)

    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), server_default=new_id, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(63), nullable=False),
        sa.Column("instructions", sa.Text(), server_default="", nullable=False),
        sa.Column("connections", TextList, server_default=empty_list, nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("effort", sa.Text(), nullable=True),
        sa.Column("autonomy", sa.Text(), nullable=True),
        now("created_at"),
        now("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_agents"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE", name="fk_agents_project"
        ),
        sa.UniqueConstraint("project_id", "name", name="uq_agents_project_name"),
    )
    op.create_table(
        "routines",
        sa.Column("id", sa.Uuid(), server_default=new_id, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("schedule", Doc, nullable=True),
        sa.Column("event", Doc, nullable=True),
        sa.Column("budget_usd", sa.Numeric(10, 4, asdecimal=False), nullable=True),
        sa.Column("max_steps", sa.Integer(), server_default=sa.text("40"), nullable=False),
        sa.Column("max_minutes", sa.Integer(), server_default=sa.text("15"), nullable=False),
        sa.Column("max_runs_per_day", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("notify", sa.Text(), server_default="app", nullable=False),
        sa.Column("catch_up", sa.Boolean(), server_default=true, nullable=False),
        sa.Column("paused", sa.Boolean(), server_default=true, nullable=False),
        sa.Column("hook_secret", sa.Text(), nullable=True),
        sa.Column("next_run_at", UTCDateTime(), nullable=True),
        sa.Column("state", Doc, server_default=empty_doc, nullable=False),
        now("created_at"),
        now("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_routines"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE", name="fk_routines_project"
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], ondelete="CASCADE", name="fk_routines_agent"
        ),
    )
    op.create_table(
        "routine_runs",
        sa.Column("id", sa.Uuid(), server_default=new_id, nullable=False),
        sa.Column("routine_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("event_key", sa.Text(), nullable=True),
        sa.Column("event", Doc, nullable=True),
        sa.Column("missed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("due_at", UTCDateTime(), nullable=True),
        sa.Column("skipped", sa.Text(), nullable=True),
        now("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_routine_runs"),
        sa.ForeignKeyConstraint(
            ["routine_id"], ["routines.id"], ondelete="CASCADE", name="fk_routine_runs_routine"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], ondelete="SET NULL", name="fk_routine_runs_task"
        ),
        sa.UniqueConstraint("routine_id", "event_key", name="uq_routine_runs_event"),
    )
    op.create_index("ix_routine_runs_routine_created", "routine_runs", ["routine_id", "created_at"])
    op.add_column(
        "projects", sa.Column("paused", sa.Boolean(), server_default=false, nullable=False)
    )
    op.add_column("tasks", sa.Column("agent_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("agent_id")
    restore_triggers("tasks")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("paused")
    op.drop_index("ix_routine_runs_routine_created", table_name="routine_runs")
    op.drop_table("routine_runs")
    op.drop_table("routines")
    op.drop_table("agents")
