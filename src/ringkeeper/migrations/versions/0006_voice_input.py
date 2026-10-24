"""Voice input over Telegram: how a message was given, "I heard" confirmations,
and notifications about routed messages and finished tasks.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("input", sa.Text(), server_default=sa.text("'text'"), nullable=False),
    )
    op.create_check_constraint("ck_messages_input", "messages", "input IN ('text', 'voice')")

    op.create_table(
        "voice_confirmations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("external_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="voice_confirmations_pkey"),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["decisions.id"], name="fk_voice_confirmations_decision",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("kind IN ('answer', 'request')", name="ck_voice_confirmations_kind"),
        sa.CheckConstraint(
            "(kind = 'answer') = (decision_id IS NOT NULL)",
            name="ck_voice_confirmations_answer_has_decision",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'redone', 'expired')",
            name="ck_voice_confirmations_status",
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)",
            name="ck_voice_confirmations_resolved_iff_not_pending",
        ),
    )  # fmt: skip
    op.create_index(
        "ix_voice_confirmations_pending", "voice_confirmations", ["expires_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )  # fmt: skip

    op.alter_column("notifications", "decision_id", nullable=True)
    for name, table in (("message_id", "messages"), ("task_id", "tasks")):
        op.add_column("notifications", sa.Column(name, sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_notifications_{name.removesuffix('_id')}", "notifications", table, [name],
            ["id"], ondelete="CASCADE",
        )  # fmt: skip
    op.create_unique_constraint(
        "uq_notifications_message", "notifications", ["message_id", "channel", "chat_id"]
    )
    op.create_unique_constraint(
        "uq_notifications_task", "notifications", ["task_id", "channel", "chat_id"]
    )
    op.create_check_constraint(
        "ck_notifications_one_subject", "notifications",
        "num_nonnulls(decision_id, message_id, task_id) = 1",
    )  # fmt: skip


def downgrade() -> None:
    op.execute("DELETE FROM notifications WHERE decision_id IS NULL")
    op.drop_constraint("ck_notifications_one_subject", "notifications")
    op.drop_constraint("uq_notifications_task", "notifications")
    op.drop_constraint("uq_notifications_message", "notifications")
    for name in ("task_id", "message_id"):
        op.drop_constraint(f"fk_notifications_{name.removesuffix('_id')}", "notifications")
        op.drop_column("notifications", name)
    op.alter_column("notifications", "decision_id", nullable=False)
    op.drop_index("ix_voice_confirmations_pending", "voice_confirmations")
    op.drop_table("voice_confirmations")
    op.drop_constraint("ck_messages_input", "messages")
    op.drop_column("messages", "input")
