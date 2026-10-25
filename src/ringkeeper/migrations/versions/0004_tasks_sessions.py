"""Tasks, worker sessions, session events and token revocations.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

task_status = postgresql.ENUM(
    "queued", "running", "succeeded", "failed", "timed_out", "stopped", "budget_exceeded",
    name="task_status", create_type=False,
)  # fmt: skip
auth_mode = postgresql.ENUM("api_key", "subscription", name="auth_mode", create_type=False)
TEXT_ARRAY: postgresql.ARRAY[str] = postgresql.ARRAY(sa.Text())


def _ts(name: str, *, now: bool = False) -> sa.Column[datetime]:
    default = sa.func.now() if now else None
    return sa.Column(name, sa.DateTime(timezone=True), server_default=default, nullable=not now)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite gets the whole 0008 schema at once (storage.sqlite_baseline)
    bind = op.get_bind()
    task_status.create(bind)
    auth_mode.create(bind)

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column[str]("status", task_status, server_default="queued", nullable=False),
        sa.Column[str]("auth_mode", auth_mode, nullable=False),
        sa.Column("max_turns", sa.Integer(), nullable=False),
        sa.Column("max_runtime_s", sa.Integer(), nullable=False),
        sa.Column("budget_usd", sa.Numeric(10, 4, asdecimal=False), nullable=True),
        sa.Column("git_branch", sa.Text(), nullable=True),
        sa.Column("git_commit", sa.Text(), nullable=True),
        sa.Column("changed_files", TEXT_ARRAY, server_default="{}", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("stop_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _ts("created_at", now=True),
        _ts("started_at"),
        _ts("finished_at"),
        sa.PrimaryKeyConstraint("id", name="tasks_pkey"),
        sa.CheckConstraint("max_turns > 0", name="ck_tasks_max_turns_positive"),
        sa.CheckConstraint("max_runtime_s > 0", name="ck_tasks_max_runtime_positive"),
        sa.CheckConstraint(
            "budget_usd IS NULL OR (budget_usd > 0 AND auth_mode = 'api_key')",
            name="ck_tasks_budget_needs_api_key",
        ),
    )
    op.create_index("ix_tasks_status_created_at", "tasks", ["status", "created_at"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column[str]("status", task_status, server_default="queued", nullable=False),
        sa.Column("sdk_session_id", sa.Text(), nullable=True),
        sa.Column("container_id", sa.Text(), nullable=True),
        sa.Column("state_volume", sa.Text(), nullable=False),
        sa.Column("token_jti", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("turns", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6, asdecimal=False), nullable=True),
        sa.Column("usage", postgresql.JSONB(), nullable=True),
        sa.Column("model_usage", postgresql.JSONB(), nullable=True),
        sa.Column("result_subtype", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _ts("created_at", now=True),
        _ts("started_at"),
        _ts("finished_at"),
        sa.PrimaryKeyConstraint("id", name="sessions_pkey"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name="fk_sessions_task", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("task_id", "ordinal", name="uq_sessions_task_ordinal"),
    )
    op.create_index("ix_sessions_status", "sessions", ["status"])

    op.create_table(
        "session_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        _ts("at", now=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="session_events_pkey"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], name="fk_session_events_session", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("session_id", "seq", name="uq_session_events_seq"),
    )

    op.create_table(
        "token_revocations",
        sa.Column("jti", sa.Text(), nullable=False),
        _ts("revoked_at", now=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("revoked_by", sa.Text(), nullable=False),
        _ts("expires_at"),
        sa.PrimaryKeyConstraint("jti", name="token_revocations_pkey"),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite gets the whole 0008 schema at once (storage.sqlite_baseline)
    op.drop_table("token_revocations")
    op.drop_table("session_events")
    op.drop_table("sessions")
    op.drop_table("tasks")
    bind = op.get_bind()
    auth_mode.drop(bind)
    task_status.drop(bind)
