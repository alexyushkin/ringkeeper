"""The Airtable, Vercel, Twilio and Gmail adapters against small fakes: what they
send, what they hand back to the agent, and what they refuse."""

import json

import httpx2
import pytest

from ringkeeper.adapters import ADAPTERS, airtable, gmail, twilio, vercel
from ringkeeper.adapters.base import Hold, Target, ToolError, ToolKind
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.services.secrets_in import check_connection_secret
from ringkeeper.vault import Secret
from tests.fake_services import (
    AIRTABLE_TOKEN,
    GMAIL_SECRET,
    TWILIO_SECRET,
    VERCEL_TOKEN,
    FakeHttp,
    FakeImap,
    FakeSmtp,
)

pytestmark = pytest.mark.anyio

BASE = "app" + "A" * 14
RECORD = "rec" + "B" * 14
ACCOUNT = "AC" + "1" * 32


def target(secret: str, name: str = "acme") -> Target:
    return Target(name, Env.DEV, None, Secret(f"file://acme/{name}", secret))


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    fake = FakeHttp()
    for module in (airtable, vercel, twilio):
        monkeypatch.setattr(module, "transport", fake.transport())
    return fake


def test_every_service_has_its_adapter_and_sends_hold_always() -> None:
    assert {
        InstanceType.AIRTABLE,
        InstanceType.VERCEL,
        InstanceType.TWILIO,
        InstanceType.GMAIL,
    } <= ADAPTERS.keys()
    holds = {
        (service, spec.name): spec.hold
        for service, adapter in ADAPTERS.items()
        for spec in adapter.tools
        if spec.kind is ToolKind.WRITE and spec.hold is not Hold.PROD
    }
    assert holds == {
        (InstanceType.TWILIO, "send_sms"): Hold.ALWAYS,
        (InstanceType.GMAIL, "send_email"): Hold.ALWAYS,
        (InstanceType.GMAIL, "create_draft"): Hold.NEVER,
        (InstanceType.N8N, "activate_workflow"): Hold.ALWAYS,
        (InstanceType.N8N, "deactivate_workflow"): Hold.ALWAYS,
        (InstanceType.N8N, "update_workflow"): Hold.ALWAYS,
        (InstanceType.N8N, "retry_execution"): Hold.ALWAYS,
        (InstanceType.N8N, "stop_execution"): Hold.ALWAYS,
        (InstanceType.MCP, "call_tool"): Hold.ALWAYS,
    }


# --- Airtable ---


async def test_airtable_lists_tables_and_records(http: FakeHttp) -> None:
    http.routes[("GET", f"/v0/meta/bases/{BASE}/tables")] = {
        "tables": [
            {
                "id": "tblX",
                "name": "Leads",
                "fields": [{"name": "Name", "type": "singleLineText", "options": {}}],
                "views": [{"name": "Open", "id": "viw1"}],
            }
        ]
    }
    http.routes[("GET", f"/v0/{BASE}/Open leads")] = {"records": [{"id": RECORD, "fields": {}}]}
    tables = await airtable.list_tables.handler(target(AIRTABLE_TOKEN), base_id=BASE)
    assert tables == {
        "tables": [
            {
                "id": "tblX",
                "name": "Leads",
                "fields": [{"name": "Name", "type": "singleLineText"}],
                "views": ["Open"],
            }
        ]
    }
    records = await airtable.list_records.handler(
        target(AIRTABLE_TOKEN), base_id=BASE, table="Open leads", filter_formula="{Status} = 'Open'"
    )
    assert records["records"][0]["id"] == RECORD
    request = http.requests[-1]
    assert request.headers["authorization"] == f"Bearer {AIRTABLE_TOKEN}"
    assert request.url.raw_path.startswith(f"/v0/{BASE}/Open%20leads?".encode())  # one path segment
    assert request.url.params["filterByFormula"] == "{Status} = 'Open'"


async def test_airtable_writes_and_refuses_bad_ids(http: FakeHttp) -> None:
    http.routes[("POST", f"/v0/{BASE}/Leads")] = {"records": [{"id": RECORD}]}
    http.routes[("DELETE", f"/v0/{BASE}/Leads")] = {"records": [{"id": RECORD, "deleted": True}]}
    await airtable.create_records.handler(
        target(AIRTABLE_TOKEN), base_id=BASE, table="Leads", records=[{"Name": "Ann"}]
    )
    assert json.loads(http.requests[-1].content) == {
        "records": [{"fields": {"Name": "Ann"}}],
        "typecast": False,
    }
    await airtable.delete_records.handler(
        target(AIRTABLE_TOKEN), base_id=BASE, table="Leads", record_ids=[RECORD]
    )
    assert http.requests[-1].url.params.get_list("records[]") == [RECORD]
    with pytest.raises(ToolError, match="isn't an Airtable record id"):
        await airtable.delete_records.handler(
            target(AIRTABLE_TOKEN), base_id=BASE, table="Leads", record_ids=["../x"]
        )
    with pytest.raises(ToolError, match="isn't an Airtable base id"):
        await airtable.list_tables.handler(target(AIRTABLE_TOKEN), base_id="app/../meta")


async def test_airtable_errors_carry_its_message(http: FakeHttp) -> None:
    http.routes[("GET", "/v0/meta/whoami")] = httpx2.Response(
        401,
        json={
            "error": {"type": "AUTHENTICATION_REQUIRED", "message": "Invalid authentication token"}
        },
    )
    with pytest.raises(ToolError, match="Airtable returned 401: Invalid authentication token"):
        await airtable.whoami(target(AIRTABLE_TOKEN))


# --- Vercel ---


async def test_vercel_reads_without_env_values(http: FakeHttp) -> None:
    http.routes[("GET", "/v10/projects/site/env")] = {
        "envs": [
            {
                "key": "DATABASE_URL",
                "value": "postgres://secret",
                "target": ["production"],
                "type": "encrypted",
            }
        ]
    }
    http.routes[("GET", "/v6/deployments")] = {
        "deployments": [
            {
                "uid": "dpl_1",
                "url": "site-abc.vercel.app",
                "state": "READY",
                "target": "production",
                "created": 1,
            }
        ]
    }
    names = await vercel.list_env_names.handler(
        target(VERCEL_TOKEN), project="site", team_id="team_9"
    )
    assert names == {
        "variables": [{"key": "DATABASE_URL", "environments": ["production"], "type": "encrypted"}]
    }
    assert "postgres://secret" not in json.dumps(names)
    assert http.requests[-1].url.params["teamId"] == "team_9"
    deployments = await vercel.list_deployments.handler(target(VERCEL_TOKEN), project="site")
    assert deployments["deployments"][0] == {
        "id": "dpl_1", "url": "site-abc.vercel.app", "state": "READY", "environment": "production",
        "created": 1, "commit": None,
    }  # fmt: skip
    assert "teamId" not in http.requests[-1].url.params


async def test_vercel_redeploy_and_rollback(http: FakeHttp) -> None:
    http.routes[("POST", "/v13/deployments")] = {"id": "dpl_2", "url": "x", "readyState": "QUEUED"}
    http.routes[("POST", "/v9/projects/site/rollback/dpl_1")] = {}
    await vercel.redeploy.handler(
        target(VERCEL_TOKEN), deployment_id="dpl_1", project="site", to_production=True
    )
    assert json.loads(http.requests[-1].content) == {
        "name": "site",
        "deploymentId": "dpl_1",
        "target": "production",
    }
    assert await vercel.rollback.handler(
        target(VERCEL_TOKEN), project="site", deployment_id="dpl_1"
    ) == {"rolled_back_to": "dpl_1"}
    with pytest.raises(ToolError, match="isn't a Vercel id"):
        await vercel.promote.handler(target(VERCEL_TOKEN), project="../v1", deployment_id="dpl_1")


# --- Twilio ---


async def test_twilio_uses_the_api_key_and_lists_messages(http: FakeHttp) -> None:
    http.routes[("GET", f"/2010-04-01/Accounts/{ACCOUNT}/Messages.json")] = {
        "messages": [
            {
                "sid": "SM" + "a" * 32,
                "from": "+15550001111",
                "to": "+15550002222",
                "body": "hi",
                "status": "delivered",
                "account_sid": ACCOUNT,
            }
        ]
    }
    page = await twilio.list_messages.handler(target(TWILIO_SECRET), to="+15550002222", limit=5)
    assert page["messages"][0]["body"] == "hi"
    assert "account_sid" not in page["messages"][0]
    request = http.requests[-1]
    assert request.headers["authorization"].startswith(
        "Basic "
    )  # SK...:secret, not the account SID
    assert request.url.params["To"] == "+15550002222"


async def test_twilio_sends_a_text(http: FakeHttp) -> None:
    http.routes[("POST", f"/2010-04-01/Accounts/{ACCOUNT}/Messages.json")] = {
        "sid": "SM" + "b" * 32,
        "status": "queued",
    }
    sent = await twilio.send_sms.handler(
        target(TWILIO_SECRET), to="+15550002222", from_number="+15550001111", body="On my way"
    )
    assert sent["status"] == "queued"
    assert http.requests[-1].content == b"To=%2B15550002222&From=%2B15550001111&Body=On+my+way"


def test_twilio_secret_shape_and_fragments() -> None:
    assert twilio.secret_fragments(TWILIO_SECRET) == ["twilio-secret-fake-77"]
    with pytest.raises(RuleViolationError, match="Account SID"):
        check_connection_secret(
            InstanceType.TWILIO, json.dumps({"account_sid": "nope", "key_secret": "x"})
        )
    assert check_connection_secret(InstanceType.TWILIO, TWILIO_SECRET) == TWILIO_SECRET


# --- Gmail ---


@pytest.fixture
def mail(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeImap, FakeSmtp]:
    imap, smtp = FakeImap(), FakeSmtp()
    monkeypatch.setattr(gmail, "imap_factory", lambda: imap)
    monkeypatch.setattr(gmail, "smtp_factory", lambda: smtp)
    return imap, smtp


async def test_gmail_searches_and_reads_without_marking_read(
    mail: tuple[FakeImap, FakeSmtp],
) -> None:
    imap, _ = mail
    found = await gmail.search_messages.handler(
        target(GMAIL_SECRET), query="from:anna invoice", mailbox="all"
    )
    assert found == {
        "messages": [
            {
                "uid": 7,
                "from": "Anna <anna@acme.example>",
                "to": "owner@example.com",
                "subject": "Invoice 42",
                "date": "Mon, 26 Oct 2026 09:00:00 +0000",
            }
        ]
    }
    # Found by its role, not its English name; quoted as Gmail lists it (imaplib doesn't quote).
    assert imap.selected == '"[Gmail]/Tous les messages"'
    assert imap.commands[0] == ("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW", b"from:anna invoice")
    message = await gmail.get_message.handler(target(GMAIL_SECRET), uid=7)
    assert message["body"] == "Please find the invoice attached."
    assert message["message_id"] == "<m42@acme.example>"
    assert all("PEEK" in c[2] for c in imap.commands if c[0] == "FETCH")
    assert imap.logged_out
    with pytest.raises(ToolError, match="no message with uid 99"):
        await gmail.get_message.handler(target(GMAIL_SECRET), uid=99)


async def test_gmail_drafts_and_sends(mail: tuple[FakeImap, FakeSmtp]) -> None:
    imap, smtp = mail
    await gmail.create_draft.handler(
        target(GMAIL_SECRET),
        to=["anna@acme.example"],
        subject="Re: Invoice 42",
        body="Thanks!",
        in_reply_to="<m42@acme.example>",
    )
    [(mailbox, flags, raw)] = imap.appended
    assert (mailbox, flags) == ('"[Gmail]/Brouillons"', "(\\Draft)")
    assert b"In-Reply-To: <m42@acme.example>" in raw
    await gmail.send_email.handler(
        target(GMAIL_SECRET), to=["anna@acme.example"], subject="Hello", body="Hi Anna"
    )
    [sent] = smtp.sent
    assert (sent["From"], sent["To"], sent["Subject"]) == (
        "owner@example.com",
        "anna@acme.example",
        "Hello",
    )


async def test_gmail_explains_a_refused_login(mail: tuple[FakeImap, FakeSmtp]) -> None:
    imap, smtp = mail
    imap.refusal = "[ALERT] Application-specific password required: https://support.google.com/"
    with pytest.raises(ToolError, match="wants an app password"):
        await gmail.search_messages.handler(target(GMAIL_SECRET))
    imap.refusal = "[AUTHENTICATIONFAILED] Invalid credentials (Failure)"
    with pytest.raises(ToolError, match="didn't accept this address and app password"):
        await gmail.search_messages.handler(target(GMAIL_SECRET))
    smtp.password = "something-else"
    with pytest.raises(ToolError, match="didn't accept this address and app password"):
        await gmail.send_email.handler(
            target(GMAIL_SECRET), to=["a@b.example"], subject="x", body="y"
        )


def test_gmail_secret_shape_and_fragments() -> None:
    assert gmail.secret_fragments(GMAIL_SECRET) == ["abcdefghijklmnop"]
    with pytest.raises(RuleViolationError, match="address and an app password"):
        check_connection_secret(InstanceType.GMAIL, json.dumps({"address": "owner@example.com"}))
