from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect

from ringkeeper import migrate
from ringkeeper.db import dispose_engines
from ringkeeper.models import Base


def test_migrations_match_models(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []


def test_downgrade_and_upgrade_round_trip(database_url: str) -> None:
    migrate.downgrade(database_url, "base")
    probe = create_engine(database_url)
    try:
        assert set(inspect(probe).get_table_names()) <= {"alembic_version"}
    finally:
        probe.dispose()
        migrate.upgrade(database_url)
        # Pooled connections hold prepared statements for the dropped tables.
        dispose_engines()

    probe = create_engine(database_url)
    try:
        tables = set(inspect(probe).get_table_names())
    finally:
        probe.dispose()
    assert {"projects", "instances", "project_instances"} <= tables


def test_projects_are_workspaces_and_repo_is_optional(engine: Engine) -> None:
    columns = {c["name"]: c for c in inspect(engine).get_columns("projects")}
    assert "worktree_path" not in columns
    assert columns["workspace_path"]["nullable"] is True
    assert columns["repo_url"]["nullable"] is True
