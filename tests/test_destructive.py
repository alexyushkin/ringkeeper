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
        ("ALTER TABLE customers DROP COLUMN legacy_id", "drops a column or constraint"),
        ("WITH gone AS (DELETE FROM t RETURNING id) SELECT count(*) FROM gone", "deletes rows"),
    ],
)
def test_destructive_sql(sql: str, why: str) -> None:
    assert destructive.reason(SUPABASE, "execute", {"sql": sql}) == why


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE customers SET plan = 'enterprise' WHERE name = 'Coyote LLC'",
        "UPDATE notes SET body = 'drop table customers; delete from x'",
        "INSERT INTO log (msg) VALUES ($$truncate me$$)",
        "UPDATE t SET x = 1 -- DROP TABLE t",
        'UPDATE "delete from" SET a = 1',
        "ALTER TABLE customers ADD COLUMN tier text",
    ],
)
def test_ordinary_writes_are_not_destructive(sql: str) -> None:
    assert destructive.reason(SUPABASE, "execute", {"sql": sql}) is None


def test_tools_named_like_deletions_are_destructive() -> None:
    assert destructive.reason(InstanceType.N8N, "delete_workflow", {"id": "1"}) == "deletes data"
    assert destructive.reason(InstanceType.N8N, "activate_workflow", {"id": "1"}) is None


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
