import uuid

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect, text

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


def test_owners_become_clients(database_url: str) -> None:
    """0016: each owner becomes a client named as it was written, and projects and
    connections then name it by its slug ("Acme" and "acme" were one client)."""
    migrate.downgrade(database_url, "0015")
    dispose_engines()
    probe = create_engine(database_url)
    try:
        with probe.begin() as connection:
            for name, owner in (("acme-web", "Acme"), ("acme-app", "acme"), ("seaside", "seaside")):
                connection.execute(
                    text("INSERT INTO projects (id, name, owner) VALUES (:id, :name, :owner)"),
                    {"id": str(uuid.uuid4()), "name": name, "owner": owner},
                )
    finally:
        probe.dispose()
    migrate.upgrade(database_url)
    dispose_engines()
    probe = create_engine(database_url)
    try:
        with probe.connect() as connection:
            found = dict(connection.execute(text("SELECT slug, name FROM clients")).tuples().all())
            owners = {
                row[0]: row[1]
                for row in connection.execute(text("SELECT name, owner FROM projects"))
            }
    finally:
        probe.dispose()
    assert found == {"acme": "Acme", "seaside": "seaside"}
    assert owners == {"acme-web": "acme", "acme-app": "acme", "seaside": "seaside"}
