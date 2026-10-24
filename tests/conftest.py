import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from interlock import migrate, scrub

POSTGRES_IMAGE = "postgres:16-alpine"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A throwaway Postgres 16, migrated to head, shared by the whole run."""
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        url: str = postgres.get_connection_url()
        migrate.upgrade(url)
        # The CLI and API resolve their engine from settings, so point them here too.
        previous = os.environ.get("INTERLOCK_DATABASE_URL")
        os.environ["INTERLOCK_DATABASE_URL"] = url
        yield url
        if previous is None:
            os.environ.pop("INTERLOCK_DATABASE_URL", None)
        else:
            os.environ["INTERLOCK_DATABASE_URL"] = previous


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


TARGET_PASSWORD = "pw-target-6b1d9f2e"  # a fake; long enough to scrub safely


@pytest.fixture(autouse=True)
def _fresh_scrubber() -> Iterator[None]:
    yield
    scrub.clear()


@pytest.fixture(scope="session")
def target_dsn(database_url: str) -> str:
    """A separate database standing in for a client's Supabase project."""
    admin = database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f"CREATE ROLE target_rw LOGIN PASSWORD '{TARGET_PASSWORD}'")
        conn.execute("CREATE DATABASE target OWNER target_rw")
    url = make_url(database_url)
    return f"postgresql://target_rw:{TARGET_PASSWORD}@{url.host}:{url.port}/target"


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never touch the real ./.interlock: each test gets its own vault and key."""
    data_dir = tmp_path / "interlock-data"
    monkeypatch.setenv("INTERLOCK_DATA_DIR", str(data_dir))
    monkeypatch.delenv("INTERLOCK_MASTER_KEY", raising=False)
    return data_dir


@pytest.fixture(autouse=True)
def _clean_tables(request: pytest.FixtureRequest) -> Iterator[None]:
    """Empty the registries after each test that touched the database.

    Truncation (not a wrapping transaction) because CLI and API tests commit
    through their own sessions.
    """
    engine: Engine | None = None
    if "database_url" in request.fixturenames:
        engine = request.getfixturevalue("engine")
    yield
    if engine is None:
        return
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE audit_events, decisions, project_instances, projects, instances CASCADE")
        )


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
