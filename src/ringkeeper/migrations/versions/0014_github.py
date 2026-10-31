"""One more connection type: GitHub (a token, on github.com or GitHub Enterprise Server).

On SQLite the enums are plain text (see storage.sqlite_baseline), so there is
nothing to change there.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # ADD VALUE can't run inside the migration's transaction block on older servers.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE instance_type ADD VALUE IF NOT EXISTS 'github'")


def downgrade() -> None:
    """Postgres can't drop a value from an enum. The extra value is harmless to 0013's
    code, and 0001's downgrade drops the whole type."""
