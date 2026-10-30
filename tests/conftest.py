import os

# Never read a developer's .env: it may switch auth modes or paths. This must
# run before ringkeeper is imported (Settings reads the choice at import time).
os.environ["RINGKEEPER_ENV_FILE"] = ""
# Test databases and fake services listen on localhost (netpolicy refuses it otherwise).
os.environ["RINGKEEPER_ALLOW_LOCAL_CONNECTIONS"] = "1"

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
import uvicorn
from sqlalchemy import Engine, make_url, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from ringkeeper import migrate, scrub
from ringkeeper.adapters import n8n
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.gateway.app import create_app
from ringkeeper.keys import load_master_key
from ringkeeper.models import Autonomy, Base, Env, InstanceType
from ringkeeper.storage import sqlite_baseline
from ringkeeper.storage.sql import make_engine
from ringkeeper.vault import Vault, get_vault
from tests.fake_n8n import API_KEY, FakeN8n
from tests.helpers import bind, make_instance, make_project

POSTGRES_IMAGE = "postgres:16-alpine"
# Which database Ringkeeper's own state lives in for this run: postgres (server
# mode, the default) or sqlite (desktop mode). Client databases that the
# Supabase connector talks to are always Postgres.
TEST_DB = os.environ.get("RINGKEEPER_TEST_DB", "postgres")
SQLITE = TEST_DB == "sqlite"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if SQLITE and item.get_closest_marker("postgres_only"):
            item.add_marker(pytest.mark.skip(reason="Ringkeeper's own database is SQLite here"))


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """A throwaway Postgres 16 for the whole run (started only if a test needs it)."""
    if os.environ.get("RINGKEEPER_TEST_NO_DOCKER") == "1":
        pytest.skip("needs a Postgres, and there's no Docker here")
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def database_url(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[str]:
    """Ringkeeper's own database, migrated to head, shared by the whole run."""
    if SQLITE:
        url = f"sqlite:///{tmp_path_factory.mktemp('db') / 'ringkeeper.db'}"
    else:
        url = request.getfixturevalue("postgres_url")
    migrate.upgrade(url)
    # The CLI and API resolve their engine from settings, so point them here too.
    previous = os.environ.get("RINGKEEPER_DATABASE_URL")
    os.environ["RINGKEEPER_DATABASE_URL"] = url
    yield url
    if previous is None:
        os.environ.pop("RINGKEEPER_DATABASE_URL", None)
    else:
        os.environ["RINGKEEPER_DATABASE_URL"] = previous


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


TARGET_PASSWORD = "pw-target-6b1d9f2e"  # a fake; long enough to scrub safely


@pytest.fixture(autouse=True)
def _fresh_scrubber() -> Iterator[None]:
    yield
    scrub.clear()


@pytest.fixture(scope="session")
def target_dsn(postgres_url: str) -> str:
    """A separate database standing in for a client's Supabase project."""
    admin = postgres_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f"CREATE ROLE target_rw LOGIN PASSWORD '{TARGET_PASSWORD}'")
        conn.execute("CREATE DATABASE target OWNER target_rw")
    url = make_url(postgres_url)
    return f"postgresql://target_rw:{TARGET_PASSWORD}@{url.host}:{url.port}/target"


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never touch the real ./.ringkeeper: each test gets its own vault and key."""
    data_dir = tmp_path / "ringkeeper-data"
    monkeypatch.setenv("RINGKEEPER_DATA_DIR", str(data_dir))
    monkeypatch.delenv("RINGKEEPER_MASTER_KEY", raising=False)
    # A developer's own personal-mode setting mustn't change what the tests expect.
    monkeypatch.delenv("RINGKEEPER_ALLOW_SUBSCRIPTION", raising=False)
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
        if not SQLITE:
            connection.execute(
                text(
                    "TRUNCATE notifications, voice_confirmations, routing_examples, settings_kv, "
                    "owner, owner_sessions, passkeys, login_attempts, "
                    "session_events, sessions, tasks, token_revocations, audit_events, "
                    "decisions, messages, threads, project_instances, projects, instances CASCADE"
                )
            )
            return
        # No TRUNCATE in SQLite, and DELETE fires the audit log's append-only
        # triggers: drop them for the cleanup and put them back.
        guards = {n: v for n, v in sqlite_baseline.TRIGGERS.items() if n.startswith("audit")}
        for name in guards:
            connection.exec_driver_sql(f"DROP TRIGGER {name}")
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
        for name, (event, table_name, body, when) in guards.items():
            connection.exec_driver_sql(
                sqlite_baseline.trigger_sql(name, event, table_name, body, when)
            )


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session
        session.rollback()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def vault(target_dsn: str) -> Vault:
    vault = get_vault()
    vault.store("file://acme/supabase", target_dsn)
    vault.store("file://globex/supabase", target_dsn)
    vault.store("file://acme/n8n", API_KEY)
    return vault


@pytest.fixture
def fake_n8n(monkeypatch: pytest.MonkeyPatch) -> FakeN8n:
    fake = FakeN8n()
    fake.add_workflow("wf1", "Sync leads", active=True)
    monkeypatch.setattr(n8n, "transport", fake.transport())
    return fake


@pytest.fixture
def registry(database_url: str, target_dsn: str, vault: Vault) -> Iterator[None]:
    with session_scope() as session:
        make_project(session, "acme-web")
        make_project(session, "globex-portal", owner="globex")
        supabase_ref = "file://acme/supabase"
        make_instance(session, "acme-supabase-dev", secret_ref=supabase_ref)
        make_instance(session, "acme-supabase-staging", env=Env.STAGING, secret_ref=supabase_ref)
        make_instance(session, "acme-supabase-prod", env=Env.PROD, secret_ref=supabase_ref)
        make_instance(
            session,
            "acme-n8n",
            type=InstanceType.N8N,
            env=Env.PROD,
            secret_ref="file://acme/n8n",
            base_url="https://n8n.acme.example",
        )
        make_instance(session, "globex-supabase-dev", secret_ref="file://globex/supabase")
        make_instance(session, "acme-supabase-unbound", secret_ref=supabase_ref)
    bind("acme-web", "acme-supabase-dev", is_default=True)
    bind("acme-web", "acme-supabase-staging", allowed_tools=["query", "list_tables"])
    bind("acme-web", "acme-supabase-prod")
    bind("acme-web", "acme-n8n")
    bind("globex-portal", "globex-supabase-dev", is_default=True)
    with psycopg.connect(target_dsn, autocommit=True) as conn:
        conn.execute("CREATE TABLE notes (id serial PRIMARY KEY, body text)")
        conn.execute("INSERT INTO notes (body) VALUES ('hello')")
    yield
    with psycopg.connect(target_dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def gateway(registry: None, vault: Vault) -> Iterator[str]:
    port = free_port()
    config = uvicorn.Config(create_app(vault), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("gateway did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def bare_gateway(database_url: str) -> Iterator[str]:
    """The real gateway, with no client connections (so no Postgres needed)."""
    del database_url
    load_master_key(get_settings())  # the gateway only reads it; tests start without one
    server = uvicorn.Server(
        uvicorn.Config(create_app(get_vault()), host="127.0.0.1", port=free_port(),
                       log_level="warning")
    )  # fmt: skip
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "gateway did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{server.config.port}"
    server.should_exit = True


CLAUDE_API_KEY = "sk-ant-api03-fake-manager-key-0001"  # fakes: right prefix, nowhere near
CLAUDE_OAUTH_TOKEN = "sk-ant-oat01-fake-manager-token-0001"  # a real credential's length


@pytest.fixture
def task_registry(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_WORKSPACES_ROOT", "/srv/workspaces")
    monkeypatch.setenv("RINGKEEPER_MAX_CONCURRENT_WORKERS", "4")
    settings = get_settings()
    vault = get_vault(settings)
    vault.store(settings.claude_api_key_ref, CLAUDE_API_KEY)
    vault.store(settings.claude_oauth_token_ref, CLAUDE_OAUTH_TOKEN)
    with session_scope() as session:
        make_project(session, "acme-web", workspace_path="acme-web", autonomy=Autonomy.STANDARD)
        make_project(
            session, "globex-portal", workspace_path="/abs/globex", autonomy=Autonomy.STRICT
        )
