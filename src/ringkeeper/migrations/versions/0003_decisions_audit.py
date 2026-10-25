"""Decisions (held prod writes) and the append-only audit log.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

decision_status = postgresql.ENUM(
    "pending", "approved", "denied", "executed", "failed", "expired",
    name="decision_status", create_type=False,
)  # fmt: skip
audit_outcome = postgresql.ENUM(
    "refused", "held", "executed", "failed", name="audit_outcome", create_type=False
)
instance_type = postgresql.ENUM(name="instance_type", create_type=False)


def _ts(name: str, *, nullable: bool = False, now: bool = False) -> sa.Column[datetime]:
    default = sa.func.now() if now else None
    return sa.Column(name, sa.DateTime(timezone=True), server_default=default, nullable=nullable)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite gets the whole 0008 schema at once (storage.sqlite_baseline)
    bind = op.get_bind()
    decision_status.create(bind)
    audit_outcome.create(bind)

    op.create_table(
        "decisions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column[str]("status", decision_status, server_default="pending", nullable=False),
        sa.Column("project", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("token_id", sa.Text(), nullable=False),
        sa.Column("instance", sa.Text(), nullable=False),
        sa.Column[str]("service", instance_type, nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("args", postgresql.JSONB(), nullable=False),
        sa.Column("args_hash", sa.Text(), nullable=False),
        sa.Column("precondition", postgresql.JSONB(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _ts("created_at", now=True),
        _ts("expires_at"),
        _ts("decided_at", nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        _ts("executed_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="decisions_pkey"),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)",
            name="ck_decisions_decided_iff_not_pending",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_decisions_expiry_after_creation"),
    )
    op.create_index("ix_decisions_status_created_at", "decisions", ["status", "created_at"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        _ts("at", now=True),
        sa.Column("project", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("token_id", sa.Text(), nullable=True),
        sa.Column("instance", sa.Text(), nullable=True),
        sa.Column[str]("service", instance_type, nullable=True),
        sa.Column("tool", sa.Text(), nullable=True),
        sa.Column[str]("outcome", audit_outcome, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("args", postgresql.JSONB(), nullable=True),
        sa.Column("args_hash", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("decision_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="audit_events_pkey"),
        sa.ForeignKeyConstraint(["decision_id"], ["decisions.id"], name="fk_audit_events_decision"),
    )
    op.create_index("ix_audit_events_project_at", "audit_events", ["project", "at"])
    op.create_index("ix_audit_events_instance_at", "audit_events", ["instance", "at"])
    op.create_index("ix_audit_events_outcome_at", "audit_events", ["outcome", "at"])

    # The audit log is append-only, enforced by the database itself.
    op.execute(
        """
        CREATE FUNCTION audit_events_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only (% rejected)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite gets the whole 0008 schema at once (storage.sqlite_baseline)
    op.execute("DROP TRIGGER audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION audit_events_append_only()")
    op.drop_table("audit_events")
    op.drop_table("decisions")
    bind = op.get_bind()
    audit_outcome.drop(bind)
    decision_status.drop(bind)
