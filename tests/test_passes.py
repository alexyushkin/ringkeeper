"""Passes, end to end: a real MCP client with a pass token against the running gateway.

Two made-up clients, each with a production database (SQLite files in the demo folder, so these
run without Postgres). A pass reaches its project's connections the way a task does, through
the same checks on every call.
"""

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from sqlalchemy import select

from ringkeeper import bridge
from ringkeeper.adapters import sqlite_file
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import (
    AuditEvent,
    AuditOutcome,
    Env,
    Instance,
    Pass,
    Project,
    ProjectInstance,
)
from ringkeeper.services import calls, passes, undo
from ringkeeper.vault import Vault, get_vault
from tests import test_web_api
from tests.helpers import bind, make_instance, make_project

ACME, GLOBEX = "acme-web", "globex-portal"
ACME_DB, GLOBEX_DB = "acme-db", "globex-db"


def _database(name: str, rows: list[tuple[int, str]]) -> str:
    folder = sqlite_file.demo_folder()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.db"
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
        conn.executemany("INSERT INTO orders VALUES (?, ?)", rows)
    conn.close()
    return f"{sqlite_file.PREFIX}{path.as_posix()}"


@pytest.fixture
def clients(bare_gateway: str) -> tuple[str, Vault]:
    vault = get_vault()
    vault.store("file://acme/db", _database(ACME_DB, [(1, "paid"), (2, "open")]))
    vault.store("file://globex/db", _database(GLOBEX_DB, [(9, "GLOBEX-ONLY")]))
    with session_scope() as session:
        make_project(session, ACME)
        make_project(session, GLOBEX, owner="globex")
        for name, owner in ((ACME_DB, "acme"), (GLOBEX_DB, "globex")):
            make_instance(
                session, name, env=Env.PROD, owner=owner, secret_ref=f"file://{owner}/db",
                config={"demo": True},
            )  # fmt: skip
    bind(ACME, ACME_DB)
    bind(GLOBEX, GLOBEX_DB)
    return bare_gateway, vault


def new_pass(vault: Vault, name: str = "acme-desktop", project: str = ACME) -> str:
    with session_scope() as session:
        passes.create(session, vault, name, project)
    return vault.resolve(passes.ref(name)).reveal()


def mcp(url: str, token: str, service: str = "database") -> Client[Any]:
    headers = {"Authorization": f"Bearer {token}"}
    return Client(StreamableHttpTransport(f"{url}/mcp/{service}", headers=headers))


def call(url: str, token: str, tool: str, args: dict[str, Any], service: str = "database") -> Any:
    async def go() -> Any:
        async with mcp(url, token, service) as client:
            return await client.call_tool(tool, args)

    return asyncio.run(go())


def audit(session_id: str) -> list[AuditEvent]:
    with session_scope() as session:
        query = select(AuditEvent).where(AuditEvent.session_id == session_id)
        return list(session.scalars(query.order_by(AuditEvent.id)).all())


def status_of(order: int, name: str = ACME_DB) -> str:
    conn = sqlite3.connect(sqlite_file.demo_folder() / f"{name}.db")
    try:
        return str(conn.execute("SELECT status FROM orders WHERE id = ?", (order,)).fetchone()[0])
    finally:
        conn.close()


def test_a_pass_reads_its_own_project(clients: tuple[str, Vault]) -> None:
    url, vault = clients
    token = new_pass(vault)
    found = call(url, token, "query", {"instance": ACME_DB, "sql": "SELECT status FROM orders"})
    assert found.structured_content["rows"] == [["paid"], ["open"]]
    assert audit("pass:acme-desktop") == []  # reads run, as a task's do
    with session_scope() as session:  # the token is only in the vault
        stored = session.scalars(select(Pass)).one()
        assert stored.token_hash != token
        assert token not in str(stored.__dict__)
        assert stored.last_used_at is not None


def test_a_pass_can_t_reach_another_client_s_connection(clients: tuple[str, Vault]) -> None:
    url, vault = clients
    token = new_pass(vault)
    with pytest.raises(ToolError, match=f"not available to project '{ACME}'"):
        call(url, token, "query", {"instance": GLOBEX_DB, "sql": "SELECT * FROM orders"})
    [row] = audit("pass:acme-desktop")
    assert row.outcome is AuditOutcome.REFUSED
    assert row.reason == f"instance '{GLOBEX_DB}' is not bound to project '{ACME}'"


def test_every_pass_call_gets_the_same_client_check(clients: tuple[str, Vault]) -> None:
    """A link to the other client's database forced into the database (past every check
    on linking) is still refused on each call from a pass, as it is from a task."""
    url, vault = clients
    token = new_pass(vault)
    with session_scope() as session:
        project = session.scalars(select(Project).where(Project.name == ACME)).one()
        other = session.scalars(select(Instance).where(Instance.name == GLOBEX_DB)).one()
        session.add(ProjectInstance(project=project, instance=other))
    for _ in range(2):
        with pytest.raises(ToolError, match="not available"):
            call(url, token, "query", {"instance": GLOBEX_DB, "sql": "SELECT * FROM orders"})
    rows = audit("pass:acme-desktop")
    assert [r.outcome for r in rows] == [AuditOutcome.REFUSED] * 2
    assert {r.reason for r in rows} == {
        f"instance '{GLOBEX_DB}' belongs to client 'globex', project '{ACME}' to 'acme'"
    }
    listed = call(url, token, "list_instances", {})
    assert GLOBEX_DB not in str(listed.structured_content)


def test_a_change_from_a_pass_is_held_approved_and_undone(clients: tuple[str, Vault]) -> None:
    url, vault = clients
    token = new_pass(vault)
    sql = "UPDATE orders SET status = 'shipped' WHERE id = 1"
    held = call(url, token, "execute", {"instance": ACME_DB, "sql": sql}).structured_content
    assert held["status"] == "held_for_approval"
    assert "check_approval" in held["message"]
    assert "resumed" not in held["message"]  # no task waits for it
    assert status_of(1) == "paid"

    waiting = call(url, token, "check_approval", {"decision_id": held["decision_id"]}, "ringkeeper")
    assert waiting.structured_content["status"] == "pending"

    asyncio.run(calls.approve(held["decision_id"], by="owner", vault=vault))
    assert status_of(1) == "shipped"
    ran = call(url, token, "check_approval", {"decision_id": held["decision_id"]}, "ringkeeper")
    assert ran.structured_content["status"] == "executed"

    with session_scope(commit=False) as session:
        [change] = [c for c in undo.for_project(session, ACME) if c["can_revert"]]
    revert = asyncio.run(calls.request_revert(str(change["id"]), vault))
    asyncio.run(calls.approve(str(revert.id), by="owner", vault=vault))
    assert status_of(1) == "paid"


def test_check_approval_shows_only_the_pass_s_own(clients: tuple[str, Vault]) -> None:
    url, vault = clients
    first = new_pass(vault)
    second = new_pass(vault, "acme-cursor")
    sql = "UPDATE orders SET status = 'x' WHERE id = 2"
    held = call(url, first, "execute", {"instance": ACME_DB, "sql": sql}).structured_content
    with pytest.raises(ToolError, match="no held call with that id for this pass"):
        call(url, second, "check_approval", {"decision_id": held["decision_id"]}, "ringkeeper")


def test_a_revoked_pass_stops_on_its_next_call(clients: tuple[str, Vault]) -> None:
    url, vault = clients
    token = new_pass(vault)
    call(url, token, "list_instances", {})
    with session_scope() as session:
        passes.revoke(session, "acme-desktop", by="owner")
    response = httpx2.post(
        f"{url}/mcp/database",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert response.status_code == 401
    assert response.json()["error_description"] == "the pass acme-desktop has been revoked"
    with session_scope() as session:
        refused = session.scalars(
            select(AuditEvent).where(AuditEvent.outcome == AuditOutcome.REFUSED)
        ).all()
    assert any(r.reason == "the pass acme-desktop has been revoked" for r in refused)


@pytest.mark.parametrize("path", ["/mcp/architect", "/model/anthropic/v1/messages"])
def test_a_pass_reaches_neither_the_architect_nor_the_model(
    clients: tuple[str, Vault], path: str
) -> None:
    url, vault = clients
    token = new_pass(vault)
    response = httpx2.post(f"{url}{path}", json={}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_a_removed_pass_leaves_nothing_behind(clients: tuple[str, Vault]) -> None:
    url, vault = clients
    token = new_pass(vault)
    with session_scope() as session:
        passes.remove(session, vault, "acme-desktop")
    with pytest.raises(Exception):  # noqa: B017, PT011 (the vault's own error)
        vault.resolve(passes.ref("acme-desktop"))
    response = httpx2.post(
        f"{url}/mcp/database", json={}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


# --- The bridge an AI app starts (ringkeeper mcp --pass) ---


def test_the_bridge_shows_the_project_s_connections_and_checks_nothing_itself(
    clients: tuple[str, Vault],
) -> None:
    url, vault = clients
    token = new_pass(vault)

    async def go() -> tuple[set[str], Any, Any]:
        kinds = await bridge.services(url, token)
        async with Client(bridge.build(url, token, kinds, "acme-desktop")) as app:
            names = {t.name for t in await app.list_tools()}
            read = await app.call_tool(
                "database_query", {"instance": ACME_DB, "sql": "SELECT id FROM orders"}
            )
            refused = await app.call_tool(
                "database_query",
                {"instance": GLOBEX_DB, "sql": "SELECT * FROM orders"},
                raise_on_error=False,
            )
        return names, read, refused

    names, read, refused = asyncio.run(go())
    assert {"database_query", "database_execute", "memory_recall"} <= names
    assert "ringkeeper_check_approval" in names
    assert not any(n.startswith("airtable_") for n in names)  # only what the project has
    assert read.structured_content["rows"] == [[1], [2]]
    assert refused.is_error  # the gateway's refusal, passed through
    assert "not available" in text_of(refused)


def test_the_bridge_says_when_ringkeeper_isn_t_running(tmp_path: Path) -> None:
    with pytest.raises(bridge.BridgeError, match="Ringkeeper isn't running"):
        bridge.gateway_of(tmp_path)
    (tmp_path / "desktop.json").write_text('{"url": "http://127.0.0.1:1"}', encoding="utf-8")
    with pytest.raises(bridge.BridgeError, match="update it"):
        bridge.gateway_of(tmp_path)


def test_the_bridge_says_when_the_pass_is_revoked(clients: tuple[str, Vault]) -> None:
    url, vault = clients
    token = new_pass(vault)
    with session_scope() as session:
        passes.revoke(session, "acme-desktop", by="owner")
    with pytest.raises(bridge.BridgeError, match="refused this pass"):
        asyncio.run(bridge.services(url, token))


def text_of(result: Any) -> str:
    return "\n".join(getattr(block, "text", "") for block in result.content)


# --- Settings > Passes ---

owner, admin, setup_code = test_web_api.owner, test_web_api.admin, test_web_api.setup_code


def test_the_app_makes_shows_and_revokes_a_pass_without_showing_its_token(
    owner: TestClient,
) -> None:
    with session_scope() as session:
        make_project(session, ACME)
    made = owner.post("/passes", json={"name": "acme-code", "project": ACME})
    assert made.status_code == 201, made.text
    shown = made.json()
    assert shown["project"] == ACME
    assert shown["last_used_at"] is None
    setup = shown["setup"]
    assert setup["server"] == "ringkeeper-acme-code"
    started = json.loads(setup["json"])["mcpServers"]["ringkeeper-acme-code"]
    assert started["command"] == sys.executable
    assert started["args"][:5] == ["-m", "ringkeeper", "mcp", "--pass", "acme-code"]
    assert started["args"][5:] == ["--data-dir", str(get_settings().data_dir)]
    assert " '--' " in setup["claude_code"]  # PowerShell's claude.ps1 would eat a bare --
    token = get_vault().resolve(passes.ref("acme-code")).reveal()
    assert passes.PREFIX not in made.text
    assert token not in owner.get("/passes").text

    assert owner.post("/passes", json={"name": "acme-code", "project": ACME}).status_code == 409
    bad = owner.post("/passes", json={"name": "Acme Code", "project": ACME})
    assert bad.status_code == 409
    assert "lowercase letters, digits and dashes" in bad.json()["detail"]
    assert owner.get("/passes/acme-code/changes").json() == []

    revoked = owner.post("/passes/acme-code/revoke")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_at"] is not None
    assert owner.delete("/passes/acme-code").status_code == 204
    assert owner.get("/passes").json()["passes"] == []
    assert owner.get("/passes/acme-code/changes").status_code == 404
    audit = test_web_api.audit_text()
    for said in ("made the pass acme-code", "revoked the pass acme-code", "removed the pass"):
        assert said in audit


def test_a_scratch_install_s_keychain_name_goes_into_the_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RINGKEEPER_KEYCHAIN_SERVICE", "RingkeeperScratch")
    started = passes.launch(get_settings(), "acme-code")
    assert started["env"] == {"RINGKEEPER_KEYCHAIN_SERVICE": "RingkeeperScratch"}
    monkeypatch.delenv("RINGKEEPER_KEYCHAIN_SERVICE")
    assert passes.launch(get_settings(), "acme-code")["env"] == {}
