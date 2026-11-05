"""Oracle Database: one more connection type.

On SQLite the enums are plain text (see storage.sqlite_baseline), so there is
nothing to change there.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # ADD VALUE can't run inside the migration's transaction block on older servers.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE instance_type ADD VALUE IF NOT EXISTS 'oracle'")


def downgrade() -> None:
    """Postgres can't drop a value from an enum. The extra value is harmless to 0023's
    code, and 0001's downgrade drops the whole type."""
