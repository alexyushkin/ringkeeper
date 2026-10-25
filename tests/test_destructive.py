"""Held writes that delete or drop data: typed confirmation, and never from Telegram."""

import json
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy.orm import Session

from ringkeeper import destructive
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Decision, InstanceType
from ringkeeper.services import decisions, inbox
from ringkeeper.telegram import api as telegram_api
from ringkeeper.telegram.api import BotApi
from ringkeeper.telegram.bridge import Bridge, callback_data
from tests.fake_telegram import BOT_TOKEN, ME, FakeTelegram

SUPABASE = InstanceType.SUPABASE


@pytest.mark.parametrize(
    ("sql", "why"),
    [
        ("DELETE FROM customers WHERE plan = 'trial'", "deletes rows"),
        ("delete\nfrom customers", "deletes rows"),
        ("TRUNCATE audit_tmp", "empties a table"),
        ("DROP TABLE customers_old", "drops a database object"),
        ("drop schema staging cascade", "drops a database object"),
        ("ALTER TABLE customers DROP COLUMN legacy_id", "changes a table's structure"),
        ("ALTER TABLE customers ADD COLUMN tier text", "changes a table's structure"),
        ("WITH gone AS (DELETE FROM t RETURNING id) SELECT count(*) FROM gone", "deletes rows"),
        # Bypasses found in review: each needs confirmation now.
        ("DO $$ BEGIN DELETE FROM customers; END $$", "uses quoting"),
        ("INSERT INTO log (msg) VALUES ($$truncate me$$)", "uses quoting"),
        ("WITH a AS (SELECT E'\\'' AS q) DELETE FROM customers WHERE ''=''", "uses quoting"),
        ("MERGE INTO c USING s ON c.id = s.id WHEN MATCHED THEN DELETE", "MERGE"),
        ("DROP OWNED BY app_user", "drops a database object"),
        ("DROP DOMAIN d CASCADE", "drops a database object"),
        ("CALL purge_old_rows()", "runs a procedure"),
        ("SELECT purge_old_rows()", "calls purge_old_rows()"),
        ("UPDATE t SET x = evil_fn()", "calls evil_fn()"),
        ("COPY customers TO STDOUT", "copies data"),
        ("D\u200bELETE FROM t", "deletes rows"),
        ("GRANT ALL ON customers TO public", "changes permissions"),
    ],
)
def test_writes_that_need_confirmation(sql: str, why: str) -> None:
    found = destructive.reason(SUPABASE, "execute", {"sql": sql})
    assert found is not None
    assert why in found


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE customers SET plan = 'enterprise' WHERE name = 'Coyote LLC'",
        "UPDATE notes SET body = 'drop table customers; delete from x'",
        "UPDATE t SET x = 1 -- DROP TABLE t",
        'UPDATE "delete from" SET a = 1',
        "INSERT INTO log (msg, at) VALUES ('hi', now())",
        "INSERT INTO t (a) VALUES (1) ON CONFLICT (a) DO UPDATE SET a = excluded.a",
        "WITH s AS (SELECT id FROM t) UPDATE t SET x = 1 FROM s WHERE t.id = s.id",
        "UPDATE t SET price = round(price * 1.1, 2), updated_at = now() WHERE id = 7",
        "UPDATE t SET x = 1 WHERE id IN (SELECT id FROM t2)",
    ],
)
def test_ordinary_writes(sql: str) -> None:
    assert destructive.reason(SUPABASE, "execute", {"sql": sql}) is None


def test_tools_fail_closed() -> None:
    assert destructive.reason(InstanceType.N8N, "activate_workflow", {"workflow_id": "1"}) is None
    assert destructive.reason(InstanceType.N8N, "deactivate_workflow", {"workflow_id": "1"}) is None
    assert (
        destructive.reason(InstanceType.N8N, "update_workflow", {}) == "replaces a whole workflow"
    )
    unknown = destructive.reason(InstanceType.N8N, "delete_workflow", {"id": "1"})
    assert unknown == "uses a tool whose effects can't be checked"
    assert destructive.reason(SUPABASE, "execute", {}) == "has no SQL to check"


def hold(session: Session, sql: str) -> Decision:
    call = ToolCall(
        caller=Caller("acme-web", "s-1", "jti-1"), service=SUPABASE, tool="execute",
        instance="acme-supabase-prod", args={"sql": sql},
    )  # fmt: skip
    return decisions.hold(session, call, "acme-supabase-prod")


@pytest.fixture
def held(database_url: str) -> str:
    with session_scope() as session:
        return str(hold(session, "DELETE FROM customers WHERE plan = 'trial'").id)


def test_hold_records_why_and_approval_needs_the_name_typed(held: str) -> None:
    with session_scope() as session:
        decision = decisions.get_decision(session, held)
        assert decision.payload == {"destructive": "deletes rows"}
        assert decision.expires_at > datetime.now(UTC) + timedelta(hours=1)

    def approve(confirm: str | None, via: inbox.Via = inbox.Via.API) -> None:
        with session_scope() as session:
            reply = inbox.Answer(choice="approve", confirm=confirm)
            inbox.answer(session, get_settings(), held, reply, by="alice", via=via)

    with pytest.raises(RuleViolationError, match=r"type the connection's name"):
        approve(None)
    with pytest.raises(RuleViolationError, match="acme-supabase-prod"):
        approve("acme-supabase")
    with pytest.raises(RuleViolationError, match="Approve it in the Ringkeeper app"):
        approve("acme-supabase-prod", inbox.Via.TELEGRAM)
    approve(" acme-supabase-prod ")
    with session_scope() as session:
        assert (decisions.get_decision(session, held).answer or {})["choice"] == "approve"


def test_denying_needs_no_confirmation(held: str) -> None:
    with session_scope() as session:
        reply = inbox.Answer(choice="deny")
        answered = inbox.answer(session, get_settings(), held, reply, by="a", via=inbox.Via.API)
        assert answered.decision.status.value == "denied"


def test_an_ordinary_write_needs_no_confirmation(database_url: str) -> None:
    with session_scope() as session:
        ident = str(hold(session, "UPDATE customers SET plan = 'gold' WHERE id = 7").id)
    with session_scope() as session:
        reply = inbox.Answer(choice="approve")
        inbox.answer(session, get_settings(), ident, reply, by="a", via=inbox.Via.TELEGRAM)


def test_telegram_shows_only_deny_and_refuses_a_forged_approve(
    held: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTelegram()
    monkeypatch.setattr(telegram_api, "transport", httpx2.MockTransport(fake.handle))
    monkeypatch.setenv("RINGKEEPER_TELEGRAM_CHAT_IDS", json.dumps([ME]))
    bridge = Bridge(BotApi(BOT_TOKEN), get_settings())
    bridge.send_new()
    [sent] = fake.sent
    assert "approve it in the Ringkeeper app" in sent["text"]
    assert "DELETE" not in sent["text"]
    [row] = sent["reply_markup"]["inline_keyboard"]
    assert [b["text"] for b in row] == ["❌ Deny"]

    with session_scope() as session:
        decision = decisions.get_decision(session, held)
        forged = callback_data(decision, 0)  # index 0 is "approve"
    fake.press(forged)
    bridge.poll_once(0)
    assert fake.callbacks[-1]["text"].startswith("Not done: This write deletes rows.")
    with session_scope() as session:
        assert decisions.get_decision(session, held).answer is None


def test_the_run_time_check_ignores_a_tampered_flag_and_needs_the_recorded_confirmation(
    held: str,
) -> None:
    """Someone with database access clears the flag and writes an approval with no
    confirmation: the gateway's claim still refuses, because it re-derives the rule
    from the held SQL and wants the typed name with the answer."""
    with session_scope() as session:
        decision = decisions.get_decision(session, held)
        decision.payload = None
        decision.answer = {"choice": "approve", "by": "mallory"}
        decision.answered_via = "api"
    with session_scope() as session, pytest.raises(RuleViolationError, match="type the"):
        decisions.claim_for_approval(session, held, "gateway")
    with session_scope() as session:
        decision = decisions.get_decision(session, held)
        decision.answer = {"choice": "approve", "by": "mallory", "confirm": "acme-supabase-prod"}
        decision.answered_via = "telegram"
    with (
        session_scope() as session,
        pytest.raises(RuleViolationError, match="in the Ringkeeper app"),
    ):
        decisions.claim_for_approval(session, held, "gateway")


def test_an_answer_through_the_app_records_the_confirmation_for_the_run(held: str) -> None:
    with session_scope() as session:
        reply = inbox.Answer(choice="approve", confirm="acme-supabase-prod")
        inbox.answer(session, get_settings(), held, reply, by="owner", via=inbox.Via.API)
    with session_scope() as session:
        claimed = decisions.claim_for_approval(session, held, "gateway")
        assert claimed.status.value == "approved"


def test_changed_arguments_never_run(database_url: str) -> None:
    with session_scope() as session:
        ident = str(hold(session, "UPDATE customers SET plan = 'gold' WHERE id = 7").id)
    with session_scope() as session:
        decision = decisions.get_decision(session, ident)
        decision.args = {"sql": "UPDATE customers SET plan = 'gold'"}  # the WHERE is gone
    with session_scope() as session, pytest.raises(RuleViolationError, match="changed after"):
        decisions.claim_for_approval(session, ident, "cli")


def test_the_cli_needs_the_name_typed_for_a_direct_approval(held: str) -> None:
    with session_scope() as session, pytest.raises(RuleViolationError, match="type the"):
        decisions.claim_for_approval(session, held, "cli")
    with session_scope() as session:
        claimed = decisions.claim_for_approval(session, held, "cli", confirm="acme-supabase-prod")
        assert claimed.status.value == "approved"
