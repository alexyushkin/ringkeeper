"""Threads, messages, the inbox (decision kinds), routing examples, Telegram
notifications, project checks and model overrides, architect-loop task fields.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

decision_kind = postgresql.ENUM(
    "route", "approve", "plan", "question", name="decision_kind", create_type=False
)
task_kind = postgresql.ENUM(
    "feature", "fix", "data", "content", "ops", name="task_kind", create_type=False
)
task_risk = postgresql.ENUM("read", "write", "prod-write", name="task_risk", create_type=False)
complexity = postgresql.ENUM("low", "medium", "high", name="complexity", create_type=False)
session_role = postgresql.ENUM("worker", "architect", name="session_role", create_type=False)
architect_phase = postgresql.ENUM(
    "planning", "awaiting_plan", "building", "reviewing", "done",
    name="architect_phase", create_type=False,
)  # fmt: skip
message_role = postgresql.ENUM("user", "router", "system", name="message_role", create_type=False)
NEW_TYPES = (
    decision_kind, task_kind, task_risk, complexity, session_role, architect_phase, message_role
)  # fmt: skip
TEXT_ARRAY: postgresql.ARRAY[str] = postgresql.ARRAY(sa.Text())
# Columns an approve decision (a held write) must carry; other kinds leave them NULL.
CALL_COLUMNS_ALL = (
    "project", "session_id", "token_id", "instance", "service", "tool", "args", "args_hash"
)  # fmt: skip


def _ts(name: str, *, now: bool = False) -> sa.Column[datetime]:
    default = sa.func.now() if now else None
    return sa.Column(name, sa.DateTime(timezone=True), server_default=default, nullable=not now)


def _uuid_pk() -> sa.Column[uuid.UUID]:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in NEW_TYPES:
        enum.create(bind)
    # New values are not used in this transaction, so ADD VALUE is safe here.
    op.execute("ALTER TYPE decision_status ADD VALUE IF NOT EXISTS 'answered'")
    op.execute("ALTER TYPE audit_outcome ADD VALUE IF NOT EXISTS 'answered'")
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'needs_input'")
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'waiting'")

    op.add_column("projects", sa.Column("checks", TEXT_ARRAY, server_default="{}", nullable=False))
    op.add_column(
        "projects", sa.Column("models", postgresql.JSONB(), server_default="{}", nullable=False)
    )

    op.create_table(
        "threads",
        _uuid_pk(),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        _ts("created_at", now=True),
        _ts("updated_at", now=True),
        sa.PrimaryKeyConstraint("id", name="threads_pkey"),
    )
    op.create_table(
        "messages",
        _uuid_pk(),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column[str]("role", message_role, nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("routing", postgresql.JSONB(), nullable=True),
        _ts("routed_at"),
        sa.Column("route_error", sa.Text(), nullable=True),
        _ts("created_at", now=True),
        sa.PrimaryKeyConstraint("id", name="messages_pkey"),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["threads.id"], name="fk_messages_thread", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_messages_thread_created_at", "messages", ["thread_id", "created_at"])
    op.create_index(
        "ix_messages_unrouted",
        "messages",
        ["created_at"],
        postgresql_where=sa.text("role = 'user' AND routed_at IS NULL"),
    )

    # Decisions become the inbox: kinds other than approve carry no call.
    op.add_column(
        "decisions",
        sa.Column[str]("kind", decision_kind, server_default="approve", nullable=False),
    )
    for column in CALL_COLUMNS_ALL:
        op.alter_column("decisions", column, nullable=True)
    not_null = " AND ".join(f"{c} IS NOT NULL" for c in CALL_COLUMNS_ALL)
    op.create_check_constraint(
        "ck_decisions_approve_has_call", "decisions", f"kind <> 'approve' OR ({not_null})"
    )
    for name in ("payload", "answer"):
        op.add_column("decisions", sa.Column(name, postgresql.JSONB(), nullable=True))
    op.add_column("decisions", sa.Column("answered_via", sa.Text(), nullable=True))
    for name in ("task_id", "thread_id", "message_id"):
        op.add_column("decisions", sa.Column(name, sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_decisions_task", "decisions", "tasks", ["task_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_decisions_thread", "decisions", "threads", ["thread_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_decisions_message", "decisions", "messages", ["message_id"], ["id"],
        ondelete="SET NULL",
    )  # fmt: skip

    op.create_table(
        "routing_examples",
        _uuid_pk(),
        sa.Column("normalized", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("project", sa.Text(), nullable=False),
        sa.Column[str]("kind", task_kind, nullable=True),
        sa.Column("decision_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=True),
        _ts("created_at", now=True),
        sa.PrimaryKeyConstraint("id", name="routing_examples_pkey"),
        sa.UniqueConstraint("normalized", name="uq_routing_examples_normalized"),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["decisions.id"], name="fk_routing_examples_decision",
            ondelete="SET NULL",
        ),
    )  # fmt: skip

    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("external_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        _ts("created_at", now=True),
        sa.PrimaryKeyConstraint("id", name="notifications_pkey"),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["decisions.id"], name="fk_notifications_decision",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("decision_id", "channel", "chat_id", name="uq_notifications_target"),
    )  # fmt: skip

    op.create_table(
        "settings_kv",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="settings_kv_pkey"),
    )

    for name in ("thread_id", "message_id"):
        op.add_column("tasks", sa.Column(name, sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_tasks_thread", "tasks", "threads", ["thread_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_tasks_message", "tasks", "messages", ["message_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column("tasks", sa.Column[str]("kind", task_kind, nullable=True))
    op.add_column("tasks", sa.Column[str]("risk", task_risk, nullable=True))
    op.add_column("tasks", sa.Column[str]("complexity", complexity, nullable=True))
    op.add_column("tasks", sa.Column("outcome", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column[str]("phase", architect_phase, nullable=True))
    for name in ("review_round", "failed_check_rounds"):
        op.add_column(
            "tasks", sa.Column(name, sa.Integer(), server_default=sa.text("0"), nullable=False)
        )
    op.add_column("tasks", sa.Column("worker_prompt", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_tasks_review_round_range", "tasks", "review_round >= 0 AND review_round <= 3"
    )

    op.add_column(
        "sessions",
        sa.Column[str]("role", session_role, server_default="worker", nullable=False),
    )
    op.add_column("sessions", sa.Column("model", sa.Text(), nullable=True))
    # Architect loop: what an architect session asked for, and a worker turn's checks.
    op.add_column("sessions", sa.Column("action", postgresql.JSONB(), nullable=True))
    op.add_column("sessions", sa.Column("checks", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    # Values added to existing enums (answered, needs_input, waiting) stay:
    # Postgres can't drop enum values, and unused values are harmless.
    op.drop_column("sessions", "checks")
    op.drop_column("sessions", "action")
    op.drop_column("sessions", "model")
    op.drop_column("sessions", "role")
    op.drop_constraint("ck_tasks_review_round_range", "tasks")
    for name in (
        "worker_prompt", "failed_check_rounds", "review_round", "phase", "summary", "outcome",
        "complexity", "risk", "kind",
    ):  # fmt: skip
        op.drop_column("tasks", name)
    op.drop_constraint("fk_tasks_message", "tasks")
    op.drop_constraint("fk_tasks_thread", "tasks")
    op.drop_column("tasks", "message_id")
    op.drop_column("tasks", "thread_id")
    op.drop_table("settings_kv")
    op.drop_table("notifications")
    op.drop_table("routing_examples")
    for name in ("fk_decisions_message", "fk_decisions_thread", "fk_decisions_task"):
        op.drop_constraint(name, "decisions")
    for name in ("message_id", "thread_id", "task_id", "answered_via", "answer", "payload"):
        op.drop_column("decisions", name)
    op.drop_constraint("ck_decisions_approve_has_call", "decisions")
    op.execute("DELETE FROM decisions WHERE kind <> 'approve'")
    for column in CALL_COLUMNS_ALL:
        op.alter_column("decisions", column, nullable=False)
    op.drop_column("decisions", "kind")
    op.drop_table("messages")
    op.drop_table("threads")
    op.drop_column("projects", "models")
    op.drop_column("projects", "checks")
    bind = op.get_bind()
    for enum in reversed(NEW_TYPES):
        enum.drop(bind)
