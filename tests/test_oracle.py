"""Oracle Database: the same checks as Postgres, with a fake driver."""

import base64
import datetime
import io
import json
import socket
import zipfile
from pathlib import Path
from typing import Any

import oracledb
import pytest

from ringkeeper import destructive, netpolicy
from ringkeeper.adapters import oracle
from ringkeeper.adapters.base import Target, ToolError, ToolKind
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.services import env_scan, secrets_in
from ringkeeper.vault import Secret

pytestmark = pytest.mark.anyio
DSN = "db.example.com:1521/orclpdb1"
PASSWORD = "fake-oracle-pass"
WHEN = datetime.datetime(2026, 1, 2, 3, 4, 5)


def wallet(*names: str) -> str:
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as z:
        for name in names:
            z.writestr(name, f"content of {name}")
    return base64.b64encode(packed.getvalue()).decode()


def anywhere(_host: str) -> None:
    return None


class FakeCursor:
    def __init__(self, world: "FakeDb") -> None:
        self.world = world
        self.description: list[tuple[str]] | None = None
        self.rowcount = 0
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, statement: str, params: list[Any] | None = None) -> None:
        self.world.statements.append((statement, list(params or [])))
        if self.world.fail_on and self.world.fail_on in statement:
            raise oracledb.DatabaseError("ORA-00001: unique constraint\nthe row's data")
        for start, (columns, rows) in self.world.answers.items():
            if statement.startswith(start):
                self.description = [(c,) for c in columns]
                self.rows = rows
                return
        self.description = None
        self.rowcount = 1

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        return self.rows[:size]

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, world: "FakeDb") -> None:
        self.world = world
        self.call_timeout = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.world)

    def commit(self) -> None:
        self.world.ended.append("commit")

    def rollback(self) -> None:
        self.world.ended.append("rollback")

    def close(self) -> None:
        self.world.ended.append("close")


class FakeDb:
    def __init__(self) -> None:
        self.statements: list[tuple[str, list[Any]]] = []
        self.ended: list[str] = []
        self.answers: dict[str, tuple[list[str], list[tuple[Any, ...]]]] = {}
        self.options: dict[str, Any] = {}
        self.files: list[str] = []
        self.fail_on = ""

    def connect(self, **options: Any) -> FakeConnection:
        self.options = options
        if folder := options.get("wallet_location"):
            self.files = sorted(p.name for p in Path(folder).iterdir())
        return FakeConnection(self)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeDb:
    world = FakeDb()
    monkeypatch.setattr(oracle, "connector", world.connect)
    monkeypatch.setattr(netpolicy, "check_host", anywhere)
    return world


def target(**more: str) -> Target:
    fields = {"dsn": DSN, "user": "app", "password": PASSWORD, **more}
    return Target("acme-db", Env.DEV, None, Secret("r", json.dumps(fields)))


@pytest.mark.parametrize(
    ("sql", "reads"),
    [
        ("select * from customers", True),
        ("SELECT NVL(name, 'x'), TO_CHAR(created, 'YYYY') FROM customers", True),
        ("select count(*) from orders where status = 'open'", True),
        ("select writes_something() from dual", False),
        ("select pkg.do_it(1) from dual", False),
        ("select * from customers for update", False),
        ("update customers set a = 1", False),
        ("begin delete from customers; end;", False),
        ("select q'[ ' ; delete from x -- ]' from dual", False),
    ],
)
def test_what_only_reads(sql: str, reads: bool) -> None:
    assert oracle.only_reads(sql) is reads


@pytest.mark.parametrize(
    ("sql", "typed"),
    [
        ("update customers set a = 1 where id = :1", False),
        ("insert into customers (id) values (:1)", False),
        ("delete from customers where id = 1", True),
        ("drop table customers", True),
        ("truncate table customers", True),
        ("merge into a using b on (a.id = b.id) when matched then update set a.x = 1", True),
        ("begin null; end;", True),
        ("declare x number; begin null; end;", True),
        ("grant select on customers to someone", True),
        ("update customers set a = q'[x]'", True),
    ],
)
def test_the_same_checks_as_postgres(sql: str, typed: bool) -> None:
    why = destructive.reason(InstanceType.ORACLE, "execute", {"sql": sql})
    assert (why is not None) is typed


def test_a_read_through_execute_doesnt_wait() -> None:
    assert oracle.execute.classify is not None
    assert oracle.execute.classify(target(), {"sql": "select 1 from dual"}) is ToolKind.READ
    assert oracle.execute.classify(target(), {"sql": "update a set b = 1"}) is ToolKind.WRITE


async def test_a_read_runs_in_a_read_only_transaction_and_is_rolled_back(db: FakeDb) -> None:
    db.answers["select"] = (["ID", "WHEN"], [(1, WHEN)])
    found = await oracle.query.handler(target(), "select id, when from t;")
    assert db.statements[0][0] == "SET TRANSACTION READ ONLY"
    assert db.statements[1][0] == "select id, when from t"  # the driver takes no semicolon
    assert found["rows"] == [[1, "2026-01-02T03:04:05"]]
    assert db.ended == ["rollback", "close"]


async def test_query_refuses_a_function_it_doesnt_know(db: FakeDb) -> None:
    with pytest.raises(ToolError, match="Use execute"):
        await oracle.query.handler(target(), "select writes_something() from dual")
    assert not db.statements


async def test_a_write_commits_and_a_failure_rolls_back(db: FakeDb) -> None:
    done = await oracle.execute.handler(target(), "update a set b = :1", [1])
    assert done["rowcount"] == 1
    assert db.ended == ["commit", "close"]
    db.ended.clear()
    db.fail_on = "update"
    with pytest.raises(ToolError) as caught:
        await oracle.execute.handler(target(), "update a set b = :1", [1])
    assert db.ended == ["rollback", "close"]
    assert str(caught.value) == "ORA-00001: unique constraint"  # never the data after it


async def test_a_name_that_doesnt_resolve_says_so(
    db: FakeDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_such_host(**_options: Any) -> None:
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(oracle, "connector", no_such_host)
    with pytest.raises(ToolError, match="check its connect string"):
        await oracle.query.handler(target(), "select 1 from dual")


async def test_a_wallet_is_unpacked_for_the_call_and_gone_after(db: FakeDb) -> None:
    packed = wallet("ewallet.pem", "tnsnames.ora", "cwallet.sso", "../../evil.txt")
    await oracle.query.handler(
        target(dsn="acme_high", wallet=packed, wallet_password="w"), "select 1 from dual"
    )
    assert db.files == ["ewallet.pem", "tnsnames.ora"]
    assert db.options["wallet_password"] == "w"
    assert db.options["config_dir"] == db.options["wallet_location"]
    assert not Path(db.options["wallet_location"]).exists()


@pytest.mark.parametrize(
    ("value", "says"),
    [("not base64 !!", "isn't a wallet"), (wallet("readme.txt"), "no ewallet.pem")],
)
def test_a_bad_wallet_says_so(value: str, says: str) -> None:
    secret = json.dumps({"dsn": "acme_high", "user": "a", "password": "p", "wallet": value})
    with pytest.raises(RuleViolationError, match=says):
        secrets_in.check_connection_secret(InstanceType.ORACLE, secret)


@pytest.mark.parametrize(
    ("dsn", "hosts"),
    [
        ("db.example.com:1521/orcl", ["db.example.com"]),
        ("tcps://db.example.com:1522/orcl", ["db.example.com"]),
        ("(description=(address=(protocol=tcps)(host=adb.example.com)(port=1522)))",
         ["adb.example.com"]),
        ("acme_high", []),
    ],
)  # fmt: skip
def test_where_a_connect_string_goes(dsn: str, hosts: list[str]) -> None:
    assert oracle.hosts_of(dsn) == hosts


def test_a_local_network_address_is_refused() -> None:
    secret = json.dumps({"dsn": "169.254.169.254:1521/x", "user": "a", "password": "p"})
    with pytest.raises(netpolicy.AddressNotAllowedError):
        secrets_in.check_connection_secret(InstanceType.ORACLE, secret)


def test_the_password_and_wallet_are_scrubbed() -> None:
    value = json.dumps({"dsn": DSN, "user": "a", "password": PASSWORD, "wallet": "abc",
                        "wallet_password": "w-pass"})  # fmt: skip
    assert set(oracle.secret_fragments(value)) == {PASSWORD, "abc", "w-pass"}


async def test_undo_puts_an_update_back_by_its_primary_key(db: FakeDb) -> None:
    db.answers["SELECT cc.column_name"] = (["COLUMN_NAME"], [("ID",)])
    db.answers['SELECT "ID"'] = (["ID", "NAME", "SEEN"], [(1, "Old", WHEN)])
    back = oracle.OracleUndo()
    args = {"sql": "update customers set name = :1, seen = :2 where id = :3",
            "params": ["New", WHEN, 1]}  # fmt: skip
    assert back.why_not(args) is None
    kept = await back.before(target(), args)
    assert kept["rows"] == [{"ID": 1, "NAME": "Old", "SEEN": {"$date": WHEN.isoformat()}}]
    select = next(s for s in db.statements if s[0].startswith('SELECT "ID"'))
    assert select == ('SELECT "ID", "NAME", "SEEN" FROM customers WHERE id = :3', [1])
    db.statements.clear()
    assert await back.restore(target(), kept) == {"restored": 1}
    statement, values = db.statements[-1]
    assert statement == 'UPDATE customers SET "NAME" = :1, "SEEN" = :2 WHERE "ID" = :3'
    assert values == ["Old", WHEN, 1]
    assert "commit" in db.ended


async def test_undo_adds_deleted_rows_back(db: FakeDb) -> None:
    db.answers["SELECT cc.column_name"] = (["COLUMN_NAME"], [("ID",)])
    db.answers["SELECT *"] = (["ID", "NAME"], [(7, "Gone")])
    back = oracle.OracleUndo()
    kept = await back.before(target(), {"sql": "delete from customers where id = 7"})
    await back.restore(target(), kept)
    assert db.statements[-1] == (
        'INSERT INTO customers ("ID", "NAME") VALUES (:1, :2)', [7, "Gone"],
    )  # fmt: skip
    assert "Adds back the 1 row" in back.describe(kept)


async def test_a_table_with_no_primary_key_says_it_cant_be_undone(db: FakeDb) -> None:
    db.answers["SELECT cc.column_name"] = (["COLUMN_NAME"], [])
    back = oracle.OracleUndo()
    with pytest.raises(Exception, match="no primary key"):
        await back.before(target(), {"sql": "delete from logs where id = 7"})


def test_what_cant_be_undone_says_so_on_the_card() -> None:
    back = oracle.OracleUndo()
    assert back.why_not({"sql": "insert into a (b) values (:1)"})
    assert back.why_not({"sql": "drop table a"})
    assert back.why_not({"sql": "update a set b = 1 where c = 2"}) is None


def test_a_projects_oracle_settings_are_found(tmp_path: Path) -> None:
    env = f"ORACLE_DSN={DSN}\nORACLE_USER=app\nORACLE_PASSWORD={PASSWORD}\n"
    (tmp_path / ".env").write_text(env, encoding="utf-8")
    found = [f for f in env_scan.scan(tmp_path) if f[0].service == "oracle"]
    assert len(found) == 1
    assert found[0][0].usable
    assert PASSWORD not in found[0][0].masked
