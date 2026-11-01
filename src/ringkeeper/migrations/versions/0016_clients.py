"""Clients of their own: `clients` (slug, and the name as typed).

A client existed only as the `owner` text of its projects and connections, so
one with only connections, or none yet, wasn't listed, and a name typed as
"Seaside Windows & Doors" was kept as its slug. Each owner becomes a client
(its slug, and the owner text as its name, which the app offers to change),
and projects and connections then name the client by its slug. Runs on both
databases.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from ringkeeper.names import slug
from ringkeeper.storage.types import UTCDateTime

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHARED_OWNER = "you"  # a shared tool's owner: not a client


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("slug", sa.String(63), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), server_default=sa.func.now(), nullable=False),
    )
    bind = op.get_bind()
    owners = [
        str(row[0])
        for table in ("projects", "instances")
        for row in bind.execute(sa.text(f"SELECT DISTINCT owner FROM {table}"))  # noqa: S608
        if row[0] and str(row[0]).strip().casefold() != SHARED_OWNER
    ]
    names: dict[str, str] = {}
    for owner in sorted(owners):
        key = slug(owner) or "client"
        # "Acme" and "acme" were already one client: keep the name with capitals.
        if key not in names or (owner != owner.lower() and names[key] == names[key].lower()):
            names[key] = owner.strip()
    clients = sa.table("clients", sa.column("slug"), sa.column("name"))
    if names:
        op.bulk_insert(clients, [{"slug": k, "name": v} for k, v in sorted(names.items())])
    for owner in set(owners):
        key = slug(owner) or "client"
        if key != owner:
            for table in ("projects", "instances"):
                bind.execute(
                    sa.text(f"UPDATE {table} SET owner = :key WHERE owner = :owner"),  # noqa: S608
                    {"key": key, "owner": owner},
                )


def downgrade() -> None:
    op.drop_table("clients")
