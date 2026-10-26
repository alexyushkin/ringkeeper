"""Two clients, two projects, two databases (think two Neon projects): each
project's agent sees and changes only its own, through the real gateway with
real worker tokens. Runs with Ringkeeper's own database on Postgres and on
SQLite (the client databases are Postgres either way)."""

# ruff: noqa: S608  (table names are this file's own constants)

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from ringkeeper.db import session_scope
from ringkeeper.models import Env
from ringkeeper.vault import Vault
from tests.helpers import bind, make_instance, make_project
from tests.test_gateway import client, text_of, token

pytestmark = pytest.mark.anyio

PASSWORD = "beta-db-password-7c1e"  # a fake, long enough to be scrubbed


@pytest.fixture
def two_clients(gateway: str, postgres_url: str, target_dsn: str, vault: Vault) -> Iterator[str]:
    """alpha-site uses the existing target database; beta-site gets one of its own."""
    admin = postgres_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f"CREATE ROLE beta_rw LOGIN PASSWORD '{PASSWORD}'")
        conn.execute("CREATE DATABASE beta OWNER beta_rw")
    url = make_url(postgres_url)
    beta_dsn = f"postgresql://beta_rw:{PASSWORD}@{url.host}:{url.port}/beta"
    for dsn, table in ((target_dsn, "alpha_orders"), (beta_dsn, "beta_orders")):
        with psycopg.connect(dsn, autocommit=True) as conn:
            name = sql.Identifier(table)
            conn.execute(sql.SQL("CREATE TABLE {} (id serial PRIMARY KEY, item text)").format(name))
            conn.execute(sql.SQL("INSERT INTO {} (item) VALUES ('first')").format(name))
    vault.store("file://alpha/db", target_dsn)
    vault.store("file://beta/db", beta_dsn)
    with session_scope() as session:
        make_project(session, "alpha-site", owner="alpha")
        make_project(session, "beta-site", owner="beta")
        make_instance(session, "alpha-db", env=Env.DEV, secret_ref="file://alpha/db", owner="alpha")
        make_instance(session, "beta-db", env=Env.DEV, secret_ref="file://beta/db", owner="beta")
    bind("alpha-site", "alpha-db", is_default=True)
    bind("beta-site", "beta-db", is_default=True)
    yield beta_dsn
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS beta WITH (FORCE)")
        conn.execute("DROP ROLE IF EXISTS beta_rw")


def rows(dsn: str, table: str) -> list[Any]:
    with psycopg.connect(dsn) as conn:
        query = sql.SQL("SELECT item FROM {} ORDER BY id").format(sql.Identifier(table))
        return [r[0] for r in conn.execute(query).fetchall()]


@pytest.mark.parametrize(
    ("project", "mine", "theirs", "my_table", "their_table"),
    [
        ("alpha-site", "alpha-db", "beta-db", "alpha_orders", "beta_orders"),
        ("beta-site", "beta-db", "alpha-db", "beta_orders", "alpha_orders"),
    ],
)
async def test_each_project_sees_and_changes_only_its_own_database(
    two_clients: str,
    gateway: str,
    target_dsn: str,
    project: str,
    mine: str,
    theirs: str,
    my_table: str,
    their_table: str,
) -> None:
    dsns = {"alpha_orders": target_dsn, "beta_orders": two_clients}
    async with client(gateway, token(project)) as mcp:
        listed = await mcp.call_tool("list_instances", {})
        assert listed.structured_content is not None
        assert [i["name"] for i in listed.structured_content["instances"]] == [mine]

        tables = await mcp.call_tool("list_tables", {})  # the default: its own database
        assert tables.structured_content is not None
        names = {t["name"] for t in tables.structured_content["tables"]}
        assert my_table in names
        assert their_table not in names

        written = await mcp.call_tool(
            "execute", {"sql": f"INSERT INTO {my_table} (item) VALUES ('second')", "instance": mine}
        )
        assert not written.is_error, text_of(written)

        for tool, args in (
            ("query", {"sql": f"SELECT * FROM {their_table}", "instance": theirs}),
            ("execute", {"sql": f"DELETE FROM {their_table}", "instance": theirs}),
        ):
            refused = await mcp.call_tool(tool, args, raise_on_error=False)
            assert refused.is_error
            assert f"instance '{theirs}' is not available to project '{project}'" in text_of(
                refused
            )

        # Its own connection can't reach the other database by name either.
        wrong = await mcp.call_tool(
            "query", {"sql": f"SELECT * FROM {their_table}"}, raise_on_error=False
        )
        assert wrong.is_error
        assert their_table in text_of(wrong)  # "relation ... does not exist", not their rows

    assert rows(dsns[my_table], my_table) == ["first", "second"]
    assert rows(dsns[their_table], their_table) == ["first"]
