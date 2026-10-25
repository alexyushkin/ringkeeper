"""Security fixes: recent password confirmation on owner sessions, the Telegram
chat a request came from, and who sent a voice note (so only they can confirm
it, and a Redo follow-up is theirs alone).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = "status IN ('pending', 'confirmed', 'redone', 'expired')"
NEW_STATUSES = "status IN ('pending', 'confirmed', 'redone', 'expired', 'used')"


def upgrade() -> None:
    op.add_column(
        "owner_sessions", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("threads", sa.Column("origin_chat_id", sa.BigInteger(), nullable=True))
    op.add_column("voice_confirmations", sa.Column("user_id", sa.BigInteger(), nullable=True))
    op.drop_constraint("ck_voice_confirmations_status", "voice_confirmations")
    op.create_check_constraint("ck_voice_confirmations_status", "voice_confirmations", NEW_STATUSES)


def downgrade() -> None:
    op.execute("UPDATE voice_confirmations SET status = 'redone' WHERE status = 'used'")
    op.drop_constraint("ck_voice_confirmations_status", "voice_confirmations")
    op.create_check_constraint("ck_voice_confirmations_status", "voice_confirmations", OLD_STATUSES)
    op.drop_column("voice_confirmations", "user_id")
    op.drop_column("threads", "origin_chat_id")
    op.drop_column("owner_sessions", "confirmed_at")
