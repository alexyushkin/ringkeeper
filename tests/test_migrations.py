from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect

from interlock import migrate
from interlock.models import Base


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

    probe = create_engine(database_url)
    try:
        tables = set(inspect(probe).get_table_names())
    finally:
        probe.dispose()
    assert {"projects", "instances", "project_instances"} <= tables
