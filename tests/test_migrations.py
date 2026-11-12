import json
import uuid

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect, text

from ringkeeper import migrate
from ringkeeper.db import dispose_engines
from ringkeeper.models import Base
from ringkeeper.storage.sqlite_baseline import TRIGGERS


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


def test_existing_connections_get_readable_titles(database_url: str) -> None:
    """0019: a connection reads as its service and its account, host or client."""
    migrate.downgrade(database_url, "0018")
    dispose_engines()
    rows: tuple[tuple[str, str, str | None, dict[str, str], str], ...] = (
        ("acme-a-yushkin", "google", None, {"email": "a@acme.example"}, "acme"),
        ("acme-n8n", "n8n", "https://n8n.acme.example/", {}, "acme"),
        ("acme-airtable", "airtable", None, {}, "acme"),
        ("acme-notion", "mcp", None, {"remote": "notion"}, "acme"),
        ("files", "mcp", None, {"source": "claude-desktop"}, "acme"),
    )
    probe = create_engine(database_url)
    try:
        with probe.begin() as connection:
            connection.execute(text("INSERT INTO clients (slug, name) VALUES ('acme', 'Acme Inc')"))
            for name, kind, url, config, owner in rows:
                connection.execute(
                    text(
                        "INSERT INTO instances (id, name, type, env, base_url, secret_ref, owner,"
                        " config) VALUES (:id, :name, :type, 'dev', :url, :ref, :owner, :config)"
                        if database_url.startswith("sqlite")
                        else "INSERT INTO instances (id, name, type, env, base_url, secret_ref,"
                        " owner, config) VALUES (:id, :name, :type, 'dev', :url, :ref, :owner,"
                        " CAST(:config AS jsonb))"
                    ),
                    {"id": str(uuid.uuid4()), "name": name, "type": kind, "url": url,
                     "ref": f"file://{name}", "owner": owner,
                     "config": json.dumps(config)},
                )  # fmt: skip
    finally:
        probe.dispose()
    migrate.upgrade(database_url)
    dispose_engines()
    probe = create_engine(database_url)
    try:
        with probe.connect() as connection:
            titles = {
                row[0]: row[1]
                for row in connection.execute(text("SELECT name, title FROM instances"))
            }
    finally:
        probe.dispose()
    assert titles == {
        "acme-a-yushkin": "Google · a@acme.example",
        "acme-n8n": "n8n · n8n.acme.example",
        "acme-airtable": "Airtable · Acme Inc",
        "acme-notion": "Notion · Acme Inc",
        "files": None,
    }


def test_connections_that_read_the_same_get_their_client_s_name(database_url: str) -> None:
    """0039: a client's connection titled like another one gets its client's name."""
    migrate.downgrade(database_url, "0038")
    dispose_engines()
    same = "Google · a@example.com"
    rows: tuple[tuple[str, str, str, dict[str, str], str, bool], ...] = (
        ("acme-google", "google", same, {"email": "a@example.com"}, "acme", False),
        ("globex-google", "google", same, {"email": "a@example.com"}, "globex", False),
        ("my-google", "google", same, {"email": "a@example.com"}, "you", True),
        ("acme-db", "supabase", "Database", {}, "acme", False),
        ("globex-db", "supabase", "database", {}, "globex", False),
        ("acme-gh", "github", "GitHub · Acme", {}, "acme", False),
    )
    probe = create_engine(database_url)
    try:
        with probe.begin() as connection:
            connection.execute(
                text("INSERT INTO clients (slug, name) VALUES ('acme', 'Acme Inc'), "
                     "('globex', 'Globex')")
            )  # fmt: skip
            for name, kind, title, config, owner, shared in rows:
                connection.execute(
                    text(
                        "INSERT INTO instances (id, name, title, type, env, secret_ref, owner,"
                        " shared, config) VALUES (:id, :name, :title, :type, 'dev', :ref,"
                        " :owner, :shared, :config)"
                        if database_url.startswith("sqlite")
                        else "INSERT INTO instances (id, name, title, type, env, secret_ref,"
                        " owner, shared, config) VALUES (:id, :name, :title, :type, 'dev', :ref,"
                        " :owner, :shared, CAST(:config AS jsonb))"
                    ),
                    {"id": str(uuid.uuid4()), "name": name, "title": title, "type": kind,
                     "ref": f"file://{name}", "owner": owner, "shared": shared,
                     "config": json.dumps(config)},
                )  # fmt: skip
    finally:
        probe.dispose()
    migrate.upgrade(database_url)
    dispose_engines()
    probe = create_engine(database_url)
    try:
        with probe.connect() as connection:
            titles = {
                row[0]: row[1]
                for row in connection.execute(text("SELECT name, title FROM instances"))
            }
    finally:
        probe.dispose()
    assert titles == {
        "acme-google": "Google · Acme Inc · a@example.com",
        "globex-google": "Google · Globex · a@example.com",
        "my-google": same,  # your own keeps its title
        "acme-db": "Database · Acme Inc",
        "globex-db": "database · Globex",
        "acme-gh": "GitHub · Acme",  # no other reads the same
    }


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
    if database_url.startswith("sqlite"):
        # Rebuilding a table to drop a column on the way down takes its triggers
        # with it; live updates must still be there after the way back up.
        probe = create_engine(database_url)
        try:
            with probe.connect() as connection:
                triggers = connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                ).scalars()
                names = set(triggers)
        finally:
            probe.dispose()
        assert set(TRIGGERS) <= names
