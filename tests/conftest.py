import os

# Never read a developer's .env: it may switch auth modes or paths. This must
# run before ringkeeper is imported (Settings reads the choice at import time).
os.environ["RINGKEEPER_ENV_FILE"] = ""

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
import uvicorn
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from ringkeeper import migrate, scrub
from ringkeeper.adapters import n8n
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.gateway.app import create_app
from ringkeeper.models import Autonomy, Env, InstanceType
from ringkeeper.vault import Vault, get_vault
from tests.fake_n8n import API_KEY, FakeN8n
from tests.helpers import bind, make_instance, make_project

POSTGRES_IMAGE = "postgres:16-alpine"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A throwaway Postgres 16, migrated to head, shared by the whole run."""
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        url: str = postgres.get_connection_url()
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
    """Never touch the real ./.ringkeeper: each test gets its own vault and key."""
    data_dir = tmp_path / "ringkeeper-data"
    monkeypatch.setenv("RINGKEEPER_DATA_DIR", str(data_dir))
    monkeypatch.delenv("RINGKEEPER_MASTER_KEY", raising=False)
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
            text(
                "TRUNCATE notifications, routing_examples, settings_kv, session_events, "
                "sessions, tasks, token_revocations, audit_events, decisions, messages, "
                "threads, project_instances, projects, instances CASCADE"
            )
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
