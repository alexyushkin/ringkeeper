"""Rename projects.worktree_path to workspace_path.

Projects are workspace folders by default; a git repo (repo_url) is optional
and was already nullable.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite gets the whole 0008 schema at once (storage.sqlite_baseline)
    op.alter_column("projects", "worktree_path", new_column_name="workspace_path")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite gets the whole 0008 schema at once (storage.sqlite_baseline)
    op.alter_column("projects", "workspace_path", new_column_name="worktree_path")
