import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ringkeeper import scrub
from ringkeeper.calls import Caller, ToolCall, args_hash
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import AuditOutcome, Decision, DecisionStatus, InstanceType
from ringkeeper.services import audit, decisions
from tests.helpers import age_decisions

CALLER = Caller(project="acme-web", session_id="s-1", token_id="t-1")
CANARY = "canary-aud1t-5e3f"


def call(
    tool: str = "execute", instance: str | None = "acme-supabase-prod", **args: object
) -> ToolCall:
    return ToolCall(CALLER, InstanceType.SUPABASE, tool, instance, dict(args))


# --- Audit log ---


def test_audit_scrubs_args_and_result(session: Session) -> None:
    scrub.register(CANARY)
    event = audit.record(
        session,
        call(sql=f"SELECT '{CANARY}'"),
        AuditOutcome.FAILED,
        reason=f"error near {CANARY}",
        result={"echo": CANARY},
    )
    assert event.args == {"sql": "SELECT '[redacted]'"}
    assert event.reason == "error near [redacted]"
    assert event.result == '{"echo": "[redacted]"}'
    assert event.args_hash == args_hash({"sql": "SELECT '[redacted]'"})


def test_audit_truncates_results(session: Session) -> None:
    event = audit.record(session, call(), AuditOutcome.EXECUTED, result="x" * 5000)
    assert event.result is not None
    assert event.result.endswith("… [3000 more chars]")
    assert len(event.result) < 2100


def test_args_hash_is_stable_and_order_independent() -> None:
    assert args_hash({"a": 1, "b": [1, 2]}) == args_hash({"b": [1, 2], "a": 1})
    assert args_hash({"a": 1}) != args_hash({"a": 2})


def test_audit_log_is_append_only(session: Session) -> None:
    audit.record(session, call(), AuditOutcome.REFUSED, reason="nope")
    session.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("UPDATE audit_events SET outcome = 'executed'"))
    session.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("DELETE FROM audit_events"))


def test_audit_filters(session: Session) -> None:
    audit.record(session, call(), AuditOutcome.HELD)
    audit.record(session, call(instance="acme-supabase-dev"), AuditOutcome.EXECUTED)
    other = ToolCall(Caller("globex-portal", "s", "t"), InstanceType.N8N, "x", "globex-n8n", {})
    audit.record(session, other, AuditOutcome.REFUSED, reason="not bound")

    assert len(audit.list_events(session)) == 3
    assert [e.instance for e in audit.list_events(session, project="acme-web")] == [
        "acme-supabase-dev",
        "acme-supabase-prod",
    ]
    assert [e.outcome for e in audit.list_events(session, instance="globex-n8n")] == [
        AuditOutcome.REFUSED
    ]
    assert len(audit.list_events(session, outcome=AuditOutcome.HELD)) == 1


def test_refused_token_has_no_project(session: Session) -> None:
    anonymous = ToolCall(Caller(None, None, None), InstanceType.SUPABASE, "query", None, {})
    event = audit.record(session, anonymous, AuditOutcome.REFUSED, reason="token expired")
    assert event.project is None


# --- Decisions ---


def test_hold_and_approve_lifecycle(session: Session) -> None:
    held = decisions.hold(session, call(sql="UPDATE t SET x = 1"), "acme-supabase-prod")
    assert held.status == DecisionStatus.PENDING
    assert held.args == {"sql": "UPDATE t SET x = 1"}
    assert (held.expires_at - held.created_at).total_seconds() == 24 * 3600

    claimed = decisions.claim_for_approval(session, str(held.id), by="ops")
    assert claimed.status == DecisionStatus.APPROVED
    assert claimed.decided_by == "ops"

    done = decisions.finish(session, claimed, result={"rowcount": 3})
    assert done.status == DecisionStatus.EXECUTED
    assert done.result == '{"rowcount": 3}'


def test_cannot_approve_twice(session: Session) -> None:
    held = decisions.hold(session, call(sql="UPDATE t SET x = 1"), "acme-supabase-prod")
    decisions.claim_for_approval(session, str(held.id))
    with pytest.raises(RuleViolationError, match="is already approved"):
        decisions.claim_for_approval(session, str(held.id))


def test_denied_cannot_be_approved(session: Session) -> None:
    held = decisions.hold(session, call(), "acme-supabase-prod")
    decisions.deny(session, str(held.id), reason="too risky")
    with pytest.raises(RuleViolationError, match="is already denied"):
        decisions.claim_for_approval(session, str(held.id))


def _age(session: Session, decision_id: object) -> None:
    age_decisions(session, str(decision_id))
    session.expire_all()


def test_expired_decision_cannot_be_approved(session: Session) -> None:
    held = decisions.hold(session, call(), "acme-supabase-prod")
    _age(session, held.id)
    with pytest.raises(RuleViolationError, match=r"expired at .* can no longer be approved"):
        decisions.claim_for_approval(session, str(held.id))


def test_expire_stale_marks_old_pending_only(session: Session) -> None:
    old = decisions.hold(session, call(), "acme-supabase-prod")
    fresh = decisions.hold(session, call(), "acme-supabase-prod")
    _age(session, old.id)
    assert decisions.expire_stale(session) == 1
    assert decisions.get_decision(session, str(old.id)).status == DecisionStatus.EXPIRED
    assert decisions.get_decision(session, str(fresh.id)).status == DecisionStatus.PENDING


def test_decision_ttl_is_configurable(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_DECISION_TTL_SECONDS", "600")
    held = decisions.hold(session, call(), "acme-supabase-prod")
    assert (held.expires_at - held.created_at).total_seconds() == 600


def test_lookup_by_prefix(session: Session) -> None:
    held = decisions.hold(session, call(), "acme-supabase-prod")
    assert decisions.get_decision(session, str(held.id)[:8]).id == held.id
    with pytest.raises(NotFoundError, match="8\\+ characters"):
        decisions.get_decision(session, "abc")
    with pytest.raises(NotFoundError, match="not found"):
        decisions.get_decision(session, "00000000")


def test_database_requires_decided_at_once_decided(session: Session) -> None:
    held = decisions.hold(session, call(), "acme-supabase-prod")
    with pytest.raises(IntegrityError, match="ck_decisions_decided_iff_not_pending"):
        session.execute(
            update(Decision).where(Decision.id == held.id).values(status=DecisionStatus.DENIED)
        )


def test_unverified_caller_cannot_create_a_decision(session: Session) -> None:
    anonymous = ToolCall(Caller(None, None, None), InstanceType.SUPABASE, "execute", "x", {})
    with pytest.raises(RuleViolationError, match="verified caller"):
        decisions.hold(session, anonymous, "x")
