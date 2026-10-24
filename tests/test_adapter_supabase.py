from collections.abc import Iterator

import psycopg
import pytest

from ringkeeper import scrub
from ringkeeper.adapters import supabase
from ringkeeper.adapters.base import Target, ToolError
from ringkeeper.models import Env
from ringkeeper.vault import Secret
from tests.conftest import TARGET_PASSWORD

pytestmark = pytest.mark.anyio


@pytest.fixture
def target(target_dsn: str) -> Iterator[Target]:
    with psycopg.connect(target_dsn, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE orders (id serial PRIMARY KEY, customer text NOT NULL, "
            "total numeric(10,2), placed_at timestamptz DEFAULT now())"
        )
        conn.execute("CREATE INDEX orders_customer ON orders (customer)")
        conn.execute("INSERT INTO orders (customer, total) VALUES ('acme', 10.50), ('acme', 3)")
        conn.execute("CREATE VIEW big_orders AS SELECT * FROM orders WHERE total > 5")
    secret = Secret("file://test/target", target_dsn, supabase.dsn_fragments(target_dsn))
    yield Target("acme-supabase-dev", Env.DEV, None, secret)
    with psycopg.connect(target_dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")


def table_exists(dsn: str, name: str) -> bool:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT to_regclass(%s) IS NOT NULL", [name]).fetchone()
    return bool(row and row[0])


async def test_list_tables(target: Target) -> None:
    result = await supabase.list_tables.handler(target)
    assert result["tables"] == [
        {"schema": "public", "name": "big_orders", "type": "view"},
        {"schema": "public", "name": "orders", "type": "table"},
    ]


async def test_describe_table(target: Target) -> None:
    result = await supabase.describe_table.handler(target, table="orders")
    assert [c["name"] for c in result["columns"]] == ["id", "customer", "total", "placed_at"]
    assert result["columns"][1] == {
        "name": "customer",
        "type": "text",
        "nullable": False,
        "default": None,
    }
    assert {c["kind"] for c in result["constraints"]} == {"primary_key"}
    assert {i["name"] for i in result["indexes"]} == {"orders_pkey", "orders_customer"}


async def test_describe_missing_table(target: Target) -> None:
    with pytest.raises(ToolError, match=r"public\.nope does not exist"):
        await supabase.describe_table.handler(target, table="nope")


async def test_query_returns_json_safe_rows(target: Target) -> None:
    result = await supabase.query.handler(
        target, sql="SELECT id, total, placed_at FROM orders WHERE customer = %s", params=["acme"]
    )
    assert result["columns"] == ["id", "total", "placed_at"]
    assert result["rows"][0][:2] == [1, "10.50"]
    assert isinstance(result["rows"][0][2], str)
    assert result["row_count"] == 2
    assert result["truncated"] is False


async def test_query_truncates(target: Target) -> None:
    result = await supabase.query.handler(target, sql="SELECT * FROM orders", max_rows=1)
    assert result["row_count"] == 1
    assert result["truncated"] is True


async def test_query_cannot_write(target: Target, target_dsn: str) -> None:
    with pytest.raises(ToolError, match="read-only transaction"):
        await supabase.query.handler(target, sql="INSERT INTO orders (customer) VALUES ('x')")
    with pytest.raises(ToolError, match="read-only transaction"):
        await supabase.query.handler(target, sql="CREATE TABLE sneaky ()")
    assert not table_exists(target_dsn, "sneaky")


async def test_query_cannot_escape_the_read_only_transaction(
    target: Target, target_dsn: str
) -> None:
    for attempt in [
        "COMMIT; CREATE TABLE escaped ()",
        "ROLLBACK; BEGIN READ WRITE; CREATE TABLE escaped (); COMMIT",
        "SET TRANSACTION READ WRITE; CREATE TABLE escaped ()",
    ]:
        with pytest.raises(ToolError):
            await supabase.query.handler(target, sql=attempt)
    assert not table_exists(target_dsn, "escaped")


async def test_query_statement_timeout(target: Target, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supabase, "READ_TIMEOUT", "200ms")
    with pytest.raises(ToolError, match="statement timeout"):
        await supabase.query.handler(target, sql="SELECT pg_sleep(5)")


async def test_execute_writes_and_commits(target: Target, target_dsn: str) -> None:
    result = await supabase.execute.handler(
        target, sql="INSERT INTO orders (customer, total) VALUES (%s, %s) RETURNING id",
        params=["globex", 7],
    )  # fmt: skip
    assert result["rowcount"] == 1
    assert result["rows"] == [[3]]
    ddl = await supabase.execute.handler(target, sql="CREATE TABLE audit_copy (id int)")
    assert ddl["rowcount"] == -1
    assert table_exists(target_dsn, "audit_copy")


async def test_execute_runs_one_statement_only(target: Target, target_dsn: str) -> None:
    with pytest.raises(ToolError, match="multiple commands"):
        await supabase.execute.handler(target, sql="DELETE FROM orders; DROP TABLE orders")
    assert table_exists(target_dsn, "orders")


async def test_execute_errors_roll_back(target: Target, target_dsn: str) -> None:
    with pytest.raises(ToolError, match="violates not-null"):
        await supabase.execute.handler(target, sql="INSERT INTO orders (total) VALUES (1)")


async def test_connection_errors_do_not_leak_the_dsn(target_dsn: str) -> None:
    bad = target_dsn.replace("/target", "/missing_db")
    secret = Secret("file://test/bad", bad, supabase.dsn_fragments(bad))
    target = Target("acme-supabase-dev", Env.DEV, None, secret)
    with pytest.raises(ToolError, match="cannot connect to instance 'acme-supabase-dev'") as exc:
        await supabase.query.handler(target, sql="SELECT 1")
    assert TARGET_PASSWORD not in scrub.scrub(str(exc.value))
    assert bad not in str(exc.value)


def test_dsn_fragments_include_encoded_password() -> None:
    dsn = "postgresql://u:p%40ss%2Fw0rd!@h:5432/d"
    assert set(supabase.dsn_fragments(dsn)) >= {"p@ss/w0rd!", "p%40ss%2Fw0rd%21"}
    assert supabase.dsn_fragments("not a dsn ===") == []


def test_tools_are_declared_read_or_write() -> None:
    kinds = {t.name: t.kind.value for t in supabase.ADAPTER.tools}
    assert kinds == {
        "list_tables": "read",
        "describe_table": "read",
        "query": "read",
        "execute": "write",
    }
