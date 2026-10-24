from typing import Any

import anyio
import psycopg
import pytest
from sqlalchemy import text
from typer.testing import CliRunner, Result

from ringkeeper.calls import Caller, ToolCall
from ringkeeper.cli.main import app
from ringkeeper.db import session_scope
from ringkeeper.models import InstanceType
from ringkeeper.services.calls import run_call
from ringkeeper.vault import Vault

runner = CliRunner()
INSERT = "INSERT INTO notes (body) VALUES ('approved write')"


def cli(*args: str) -> Result:
    return runner.invoke(app, list(args))


def held(vault: Vault, sql: str = INSERT) -> str:
    call = ToolCall(
        Caller("acme-web", "s-1", "t-1"), InstanceType.SUPABASE, "execute", "acme-supabase-prod",
        {"sql": sql},
    )  # fmt: skip

    async def run() -> dict[str, Any]:
        return await run_call(call, vault)

    return str(anyio.run(run)["decision_id"])


def bodies(dsn: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        return [row[0] for row in conn.execute("SELECT body FROM notes ORDER BY id")]


@pytest.mark.usefixtures("registry")
def test_list_show_approve(vault: Vault, target_dsn: str) -> None:
    decision_id = held(vault)
    listed = cli("decision", "list")
    assert listed.exit_code == 0, listed.output
    assert decision_id[:8] in listed.stdout
    assert "pending" in listed.stdout

    shown = cli("decision", "show", decision_id[:8])
    assert shown.exit_code == 0, shown.output
    assert INSERT in shown.stdout
    assert "acme-supabase-prod (supabase)" in shown.stdout

    approved = cli("decision", "approve", decision_id, "--by", "ops-lead")
    assert approved.exit_code == 0, approved.output
    assert f"executed decision {decision_id}" in approved.stdout
    assert bodies(target_dsn) == ["hello", "approved write"]

    assert "no pending decisions" in cli("decision", "list").stdout
    executed = cli("decision", "list", "--status", "executed")
    assert decision_id[:8] in executed.stdout


@pytest.mark.usefixtures("registry")
def test_deny(vault: Vault, target_dsn: str) -> None:
    decision_id = held(vault)
    denied = cli("decision", "deny", decision_id, "--reason", "wrong table")
    assert denied.exit_code == 0, denied.output
    again = cli("decision", "approve", decision_id)
    assert again.exit_code == 1
    assert "already denied" in again.stderr
    assert bodies(target_dsn) == ["hello"]


@pytest.mark.usefixtures("registry")
def test_expired_cannot_be_approved(vault: Vault) -> None:
    decision_id = held(vault)
    with session_scope() as session:
        session.execute(
            text(
                "UPDATE decisions SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 day'"
            )
        )
    result = cli("decision", "approve", decision_id)
    assert result.exit_code == 1
    assert "can no longer be approved" in result.stderr
    assert decision_id[:8] in cli("decision", "list", "--status", "expired").stdout


@pytest.mark.usefixtures("registry")
def test_failed_approval_exits_nonzero(vault: Vault) -> None:
    decision_id = held(vault, sql="DELETE FROM missing_table")
    result = cli("decision", "approve", decision_id)
    assert result.exit_code == 1
    assert "failed" in result.stderr
    assert "does not exist" in result.stderr


@pytest.mark.usefixtures("registry")
def test_audit_list_and_filters(vault: Vault) -> None:
    decision_id = held(vault)
    cli("decision", "approve", decision_id)
    everything = cli("audit", "list")
    assert everything.exit_code == 0, everything.output
    lines = everything.stdout.splitlines()
    assert lines[0].split() == [
        "AT",
        "OUTCOME",
        "PROJECT",
        "INSTANCE",
        "TOOL",
        "DECISION",
        "DETAIL",
    ]
    assert "executed" in lines[1]
    assert "held" in lines[2]

    held_only = cli("audit", "list", "--outcome", "held", "--project", "acme-web")
    assert "held" in held_only.stdout
    assert "executed" not in held_only.stdout
    assert "no audit events" in cli("audit", "list", "--instance", "globex-supabase-dev").stdout
    assert "no audit events" in cli("audit", "list", "--since", "2030-01-01T00:00:00").stdout
    assert "invalid --since" in cli("audit", "list", "--since", "soon").stderr


def test_decision_show_missing(database_url: str) -> None:
    result = cli("decision", "show", "deadbeef")
    assert result.exit_code == 1
    assert "decision 'deadbeef' not found" in result.stderr
