"""Two more connection types: Twilio and Gmail (IMAP/SMTP with an app password).

On SQLite the enums are plain text (see storage.sqlite_baseline), so there is
nothing to change there.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # ADD VALUE can't run inside the migration's transaction block on older servers.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE instance_type ADD VALUE IF NOT EXISTS 'twilio'")
        op.execute("ALTER TYPE instance_type ADD VALUE IF NOT EXISTS 'gmail'")


def downgrade() -> None:
    """Postgres can't drop a value from an enum. The extra values are harmless to
    0008's code, and 0001's downgrade drops the whole type."""
