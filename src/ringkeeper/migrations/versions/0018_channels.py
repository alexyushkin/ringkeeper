"""Notification channels: `channels` (Slack and Discord) and
`notifications.external_ref`.

A channel is your own Slack app or Discord bot (its tokens in the vault), where it
posts and who may answer from it, and what it sends. Telegram stays in settings;
its prefs live in settings_kv. Runs on both databases.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.sqlite_baseline import NEW_UUID
from ringkeeper.storage.types import Doc, UTCDateTime

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    new_id = sa.text("gen_random_uuid()") if postgres else NEW_UUID
    true = sa.text("true") if postgres else sa.text("1")

    def now(name: str) -> sa.Column[object]:
        return sa.Column(name, UTCDateTime(), server_default=sa.func.now(), nullable=False)

    op.create_table(
        "channels",
        sa.Column("id", sa.Uuid(), server_default=new_id, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=true, nullable=False),
        sa.Column("config", Doc, server_default="{}", nullable=False),
        sa.Column("prefs", Doc, server_default="{}", nullable=False),
        now("since"),
        now("created_at"),
        now("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_channels"),
    )
    op.add_column("notifications", sa.Column("external_ref", sa.Text(), nullable=True))
    # A routine's summary goes to every channel that sends routine results.
    op.execute("UPDATE routines SET notify = 'channels' WHERE notify = 'telegram'")


def downgrade() -> None:
    op.execute("UPDATE routines SET notify = 'telegram' WHERE notify = 'channels'")
    with op.batch_alter_table("notifications") as batch:
        batch.drop_column("external_ref")
    op.drop_table("channels")
