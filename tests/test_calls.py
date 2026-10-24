"""Rule tests for the call pipeline, in the order the checks run."""

from typing import Any

import psycopg
import pytest
from sqlalchemy import select, text

from interlock.calls import Caller, ToolCall
from interlock.db import session_scope
from interlock.errors import RuleViolationError
from interlock.models import AuditEvent, AuditOutcome, DecisionStatus, InstanceType
from interlock.services import bindings, decisions, projects
from interlock.services.calls import RefusalError, approve, deny, run_call
from interlock.vault import Vault
from tests.fake_n8n import FakeN8n
from tests.helpers import bind

pytestmark = pytest.mark.anyio

ACME = Caller("acme-web", "session-1", "token-1")
SUPABASE = InstanceType.SUPABASE


def call(
    tool: str,
    instance: str | None = None,
    *,
    caller: Caller = ACME,
    service: InstanceType = SUPABASE,
    **args: Any,
) -> ToolCall:
    return ToolCall(caller, service, tool, instance, args)


def audit_rows() -> list[AuditEvent]:
    with session_scope() as session:
        return list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)).all())


def note_count(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT count(*) FROM notes").fetchone()
    return int(row[0]) if row else -1


async def refused(tool_call: ToolCall, vault: Vault, match: str) -> AuditEvent:
    with pytest.raises(RefusalError, match=match):
        await run_call(tool_call, vault)
    event = audit_rows()[-1]
    assert event.outcome is AuditOutcome.REFUSED
    return event


INSERT = "INSERT INTO notes (body) VALUES ('from agent')"


# --- 1. valid token (no verified caller) ---


async def test_no_verified_caller_is_refused(registry: None, vault: Vault) -> None:
    anonymous = Caller(None, None, None)
    event = await refused(call("query", caller=anonymous, sql="SELECT 1"), vault, "worker token")
    assert event.project is None


async def test_token_for_a_deleted_project_is_refused(registry: None, vault: Vault) -> None:
    with session_scope() as session:
        projects.remove_project(session, "acme-web")
    await refused(call("query", sql="SELECT 1"), vault, "no longer exists")


# --- 2. instance exists (and the default rule for reads) ---


async def test_read_without_instance_uses_the_default(registry: None, vault: Vault) -> None:
    result = await run_call(call("query", sql="SELECT body FROM notes"), vault)
    assert result["rows"] == [["hello"]]
    assert audit_rows() == []  # successful reads aren't audited


async def test_read_without_instance_and_no_default(registry: None, vault: Vault) -> None:
    event = await refused(
        call("list_workflows", service=InstanceType.N8N), vault, "has no default n8n instance"
    )
    assert event.reason == "read without instance and no default"


async def test_write_needs_an_explicit_instance(
    registry: None, target_dsn: str, vault: Vault
) -> None:
    event = await refused(call("execute", sql=INSERT), vault, "needs an explicit instance")
    assert event.reason == "write without an explicit instance"
    assert note_count(target_dsn) == 1


async def test_missing_instance(registry: None, vault: Vault) -> None:
    event = await refused(
        call("query", "ghost-db", sql="SELECT 1"), vault, "'ghost-db' is not available"
    )
    assert event.reason == "instance 'ghost-db' does not exist"


# --- 3. instance bound to the token's project ---


async def test_other_clients_instance_is_refused(registry: None, vault: Vault) -> None:
    event = await refused(
        call("query", "globex-supabase-dev", sql="SELECT 1"),
        vault,
        "instance 'globex-supabase-dev' is not available to project 'acme-web'",
    )
    assert event.reason == "instance 'globex-supabase-dev' is not bound to project 'acme-web'"
    assert event.project == "acme-web"
    assert event.instance == "globex-supabase-dev"


async def test_missing_and_unbound_look_the_same_to_the_agent(registry: None, vault: Vault) -> None:
    messages: list[str] = []
    for name in ("ghost-db", "acme-supabase-unbound"):
        with pytest.raises(RefusalError) as excinfo:
            await run_call(call("query", name, sql="SELECT 1"), vault)
        messages.append(str(excinfo.value).replace(name, "<name>"))
    assert messages[0] == messages[1]


async def test_wrong_service_endpoint(registry: None, vault: Vault) -> None:
    await refused(call("query", "acme-n8n", sql="SELECT 1"), vault, "is a n8n instance")


# --- 4. tool in the binding's allowed_tools ---


async def test_tool_not_allowed(registry: None, target_dsn: str, vault: Vault) -> None:
    event = await refused(
        call("execute", "acme-supabase-staging", sql=INSERT),
        vault,
        "tool 'execute' is not allowed on instance 'acme-supabase-staging'.*Allowed: query",
    )
    assert event.reason == "tool not in allowed_tools"
    assert note_count(target_dsn) == 1


async def test_allowed_tool_runs(registry: None, vault: Vault) -> None:
    result = await run_call(call("list_tables", "acme-supabase-staging"), vault)
    assert result["tables"] == [{"schema": "public", "name": "notes", "type": "table"}]


async def test_binding_check_comes_before_tool_check(registry: None, vault: Vault) -> None:
    bind("globex-portal", "globex-supabase-dev", allowed_tools=["list_tables"])
    await refused(call("execute", "globex-supabase-dev", sql=INSERT), vault, "is not available")


# --- Writes: dev/staging run, prod is held ---


async def test_dev_write_runs_and_is_audited(registry: None, target_dsn: str, vault: Vault) -> None:
    result = await run_call(call("execute", "acme-supabase-dev", sql=INSERT), vault)
    assert result["rowcount"] == 1
    assert note_count(target_dsn) == 2
    [event] = audit_rows()
    assert (event.outcome, event.instance, event.tool) == (
        AuditOutcome.EXECUTED,
        "acme-supabase-dev",
        "execute",
    )
    assert event.args == {"sql": INSERT}
    assert event.session_id == "session-1"


async def test_failed_write_is_audited(registry: None, vault: Vault) -> None:
    with pytest.raises(Exception, match="does not exist"):
        await run_call(call("execute", "acme-supabase-dev", sql="DELETE FROM nope"), vault)
    [event] = audit_rows()
    assert event.outcome is AuditOutcome.FAILED
    assert event.reason is not None
    assert "does not exist" in event.reason


async def test_prod_read_runs_without_approval(registry: None, vault: Vault) -> None:
    result = await run_call(call("query", "acme-supabase-prod", sql="SELECT 1 AS one"), vault)
    assert result["rows"] == [[1]]


async def test_prod_write_is_held(registry: None, target_dsn: str, vault: Vault) -> None:
    result = await run_call(call("execute", "acme-supabase-prod", sql=INSERT), vault)
    assert result["status"] == "held_for_approval"
    assert "Held for approval" in result["message"]
    assert note_count(target_dsn) == 1  # nothing ran

    [event] = audit_rows()
    assert event.outcome is AuditOutcome.HELD
    assert str(event.decision_id) == result["decision_id"]
    with session_scope() as session:
        decision = decisions.get_decision(session, result["decision_id"])
        assert decision.status is DecisionStatus.PENDING
        assert decision.args == {"sql": INSERT}


async def test_preapproved_prod_write_runs(registry: None, target_dsn: str, vault: Vault) -> None:
    bind("acme-web", "acme-supabase-prod", preapproved_writes=["execute"])
    result = await run_call(call("execute", "acme-supabase-prod", sql=INSERT), vault)
    assert result["rowcount"] == 1
    assert note_count(target_dsn) == 2
    assert [e.outcome for e in audit_rows()] == [AuditOutcome.EXECUTED]


# --- Approval ---


async def held(vault: Vault, sql: str = INSERT) -> str:
    result = await run_call(call("execute", "acme-supabase-prod", sql=sql), vault)
    return str(result["decision_id"])


async def test_approve_executes_and_records(registry: None, target_dsn: str, vault: Vault) -> None:
    decision_id = await held(vault)
    decision = await approve(decision_id, by="ops", vault=vault)
    assert decision.status is DecisionStatus.EXECUTED
    assert decision.decided_by == "ops"
    assert decision.result is not None
    assert '"rowcount": 1' in decision.result
    assert note_count(target_dsn) == 2
    outcomes = [(e.outcome, str(e.decision_id)) for e in audit_rows()]
    assert outcomes == [(AuditOutcome.HELD, decision_id), (AuditOutcome.EXECUTED, decision_id)]


async def test_approve_twice_is_refused(registry: None, vault: Vault) -> None:
    decision_id = await held(vault)
    await approve(decision_id, vault=vault)
    with pytest.raises(RuleViolationError, match="already executed"):
        await approve(decision_id, vault=vault)


async def test_approve_rechecks_the_binding(registry: None, target_dsn: str, vault: Vault) -> None:
    decision_id = await held(vault)
    with session_scope() as session:
        bindings.remove_binding(session, "acme-web", "acme-supabase-prod")
    decision = await approve(decision_id, vault=vault)
    assert decision.status is DecisionStatus.FAILED
    assert decision.error is not None
    assert decision.error.startswith("no longer allowed")
    assert note_count(target_dsn) == 1
    assert audit_rows()[-1].outcome is AuditOutcome.REFUSED


async def test_expired_decision_cannot_be_approved(registry: None, vault: Vault) -> None:
    decision_id = await held(vault)
    with session_scope() as session:
        session.execute(
            text(
                "UPDATE decisions SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 day'"
            )
        )
    with pytest.raises(RuleViolationError, match=r"expired at .* can no longer be approved"):
        await approve(decision_id, vault=vault)
    with session_scope() as session:
        assert decisions.get_decision(session, decision_id).status is DecisionStatus.EXPIRED


async def test_deny(registry: None, target_dsn: str, vault: Vault) -> None:
    decision_id = await held(vault)
    denied = deny(decision_id, by="ops", reason="not today")
    assert denied.status is DecisionStatus.DENIED
    with pytest.raises(RuleViolationError, match="already denied"):
        await approve(decision_id, vault=vault)
    assert note_count(target_dsn) == 1


async def test_failed_execution_on_approval(registry: None, vault: Vault) -> None:
    decision_id = await held(vault, sql="DELETE FROM missing_table")
    decision = await approve(decision_id, vault=vault)
    assert decision.status is DecisionStatus.FAILED
    assert decision.error is not None
    assert "does not exist" in decision.error
    assert audit_rows()[-1].outcome is AuditOutcome.FAILED


# --- n8n: stale approvals can't overwrite newer edits ---

WORKFLOW_UPDATE: dict[str, Any] = {
    "workflow_id": "wf1",
    "name": "Sync leads v2",
    "nodes": [],
    "connections": {},
}


async def test_update_workflow_on_prod_is_held_with_its_updated_at(
    registry: None, vault: Vault, fake_n8n: FakeN8n
) -> None:
    result = await run_call(
        call("update_workflow", "acme-n8n", service=InstanceType.N8N, **WORKFLOW_UPDATE), vault
    )
    with session_scope() as session:
        decision = decisions.get_decision(session, result["decision_id"])
        assert decision.precondition == {"updatedAt": fake_n8n.workflows["wf1"]["updatedAt"]}
    assert fake_n8n.workflows["wf1"]["name"] == "Sync leads"


async def test_approval_runs_when_workflow_is_unchanged(
    registry: None, vault: Vault, fake_n8n: FakeN8n
) -> None:
    result = await run_call(
        call("update_workflow", "acme-n8n", service=InstanceType.N8N, **WORKFLOW_UPDATE), vault
    )
    decision = await approve(result["decision_id"], vault=vault)
    assert decision.status is DecisionStatus.EXECUTED
    assert fake_n8n.workflows["wf1"]["name"] == "Sync leads v2"
    assert fake_n8n.requests[-1].url.params["publishIfActive"] == "false"


async def test_stale_approval_is_refused(registry: None, vault: Vault, fake_n8n: FakeN8n) -> None:
    result = await run_call(
        call("update_workflow", "acme-n8n", service=InstanceType.N8N, **WORKFLOW_UPDATE), vault
    )
    fake_n8n.workflows["wf1"].update(name="Edited by a human", updatedAt="2026-10-24T11:00:00Z")

    decision = await approve(result["decision_id"], vault=vault)
    assert decision.status is DecisionStatus.FAILED
    assert decision.error is not None
    assert decision.error.startswith("stale: the target changed")
    assert fake_n8n.workflows["wf1"]["name"] == "Edited by a human"  # not overwritten
    assert not any(r.method == "PUT" for r in fake_n8n.requests)
    assert audit_rows()[-1].outcome is AuditOutcome.REFUSED
