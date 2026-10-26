"""A project's link to a connection can be narrowed: `project_instances.scope`.

{"project": "...", "branch": "..."} and the like (ringkeeper.remote): the
gateway pins those IDs in every call through the link. Empty: the whole
connection. Runs on both databases.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.storage.types import Doc

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("project_instances", sa.Column("scope", Doc, server_default="{}", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("project_instances") as batch:
        batch.drop_column("scope")
