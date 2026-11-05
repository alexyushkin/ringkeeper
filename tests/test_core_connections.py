"""SMTP, Resend, a mailbox, any HTTP API and file storage, against fakes: what each sends,
and what each kind of action needs. Reads run, drafts and ordinary writes wait for approval,
and what can't be taken back needs the name typed, whatever else would let it through."""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Any

import httpx2
import pytest

from ringkeeper import destructive, netpolicy, remote, sensitive
from ringkeeper.adapters import ADAPTERS, http_api, mailbox, resend, smtp, storage
from ringkeeper.adapters.base import Hold, Target, ToolError, ToolKind
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import AuditEvent, AuditOutcome, DecisionStatus, Env, InstanceType, Trust
from ringkeeper.services import decisions, inbox, trusts
from ringkeeper.services.calls import RefusalError, approve, run_call
from ringkeeper.services.secrets_in import check_connection_secret
from ringkeeper.vault import Secret, Vault, get_vault
from tests.fake_services import MAILBOXES, FakeHttp, FakeImap, FakeSmtp
from tests.helpers import bind, make_instance, make_project

pytestmark = pytest.mark.anyio

ACME = Caller("acme-web", "session-1", "token-1")
SMTP_SECRET = json.dumps({
    "host": "smtp.example.com", "port": 587, "security": "starttls", "username": "u",
    "password": "smtp-password-fake", "from_address": "hello@acme.example",
    "test_address": "owner@acme.example",
})  # fmt: skip
RESEND_SECRET = json.dumps({
    "api_key": "re_fake_key_123456", "from_address": "hello@acme.example",
    "test_address": "owner@acme.example",
})  # fmt: skip
MAILBOX_SECRET = json.dumps({
    "preset": "zoho", "address": "me@acme.example", "password": "abcd efgh ijkl mnop",
    "test_address": "owner@acme.example",
})  # fmt: skip
HTTP_SECRET = json.dumps({"auth": "bearer", "token": "http-token-fake-123"})
S3_SECRET = json.dumps({
    "provider": "aws", "access_key": "AKIAFAKE", "secret_key": "s3-secret-fake-456",
    "region": "us-east-1",
})  # fmt: skip
BLOB_SECRET = json.dumps({"provider": "vercel_blob", "token": "vercel_blob_rw_fake_789"})
MANY = [f"p{n}@acme.example" for n in range(11)]


def target(secret: str, base_url: str | None = None) -> Target:
    return Target("acme", Env.DEV, base_url, Secret("file://acme/x", secret))


@dataclass
class FakeServer(FakeImap):
    """An IMAP server that also moves messages."""

    capabilities: tuple[str, ...] = ("IMAP4REV1", "MOVE")
    moved: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def list(self) -> tuple[str, list[bytes]]:
        return "OK", [*MAILBOXES, b'(\\HasNoChildren \\Trash) "/" "Corbeille"']

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected = mailbox
        return "OK", [b"1"]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        if command == "MOVE":
            self.moved.append((args[0], args[1]))
            return "OK", [b"moved"]
        return super().uid(command, *args)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeServer, FakeSmtp]:
    imap, sender = FakeServer(), FakeSmtp(password="smtp-password-fake")

    def any_imap(_host: str, _port: int) -> FakeServer:
        return imap

    def any_smtp(*_args: object) -> FakeSmtp:
        return sender

    def any_host(_host: str) -> None:
        return None

    monkeypatch.setattr(mailbox, "imap_factory", any_imap)
    monkeypatch.setattr(smtp, "smtp_factory", any_smtp)
    monkeypatch.setattr(netpolicy, "check_host", any_host)
    return imap, sender


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> FakeHttp:
    fake = FakeHttp()
    for module in (resend, http_api, storage):
        monkeypatch.setattr(module, "transport", fake.transport())
    return fake


@pytest.fixture
def vault(database_url: str) -> Vault:
    """No client database here, so these run on SQLite too."""
    with session_scope() as session:
        make_project(session, "acme-web")
    return get_vault()


@pytest.fixture
def linked(vault: Vault) -> None:
    """One connection of each type, on test data: where writes usually run by themselves."""
    connections = (
        ("acme-smtp", InstanceType.SMTP, SMTP_SECRET, None),
        ("acme-resend", InstanceType.RESEND, RESEND_SECRET, None),
        ("acme-mail", InstanceType.MAILBOX, MAILBOX_SECRET, None),
        ("acme-api", InstanceType.HTTP, HTTP_SECRET, "https://api.acme.example/v1"),
        ("acme-files", InstanceType.S3, S3_SECRET, None),
    )
    for name, kind, secret, base_url in connections:
        vault.store(f"file://acme/{name}", secret)
        with session_scope() as session:
            make_instance(
                session, name, type=kind, secret_ref=f"file://acme/{name}", base_url=base_url
            )
        bind("acme-web", name)


def call(service: InstanceType, tool: str, instance: str, **args: Any) -> ToolCall:
    return ToolCall(ACME, service, tool, instance, args)


# --- Every write says what it needs ---


NEW = (InstanceType.SMTP, InstanceType.RESEND, InstanceType.MAILBOX, InstanceType.HTTP,
       InstanceType.S3)  # fmt: skip


def test_every_new_write_waits_or_is_a_test_send() -> None:
    for service in NEW:
        for spec in ADAPTERS[service].tools:
            if spec.kind is not ToolKind.WRITE:
                continue
            if spec.name == "send_test_email":
                assert spec.hold is Hold.NEVER
                continue
            assert spec.hold is Hold.ALWAYS, spec.name
            assert spec.typed is not None, spec.name


@pytest.mark.parametrize(
    ("service", "tool", "args", "typed"),
    [
        (InstanceType.SMTP, "send_email", {"to": ["a@b.example"]}, False),
        (InstanceType.SMTP, "send_email", {"to": MANY}, True),
        (InstanceType.SMTP, "send_email", {"to": MANY[:5], "bcc": MANY[5:]}, True),
        (InstanceType.RESEND, "send_email", {"to": MANY}, True),
        (InstanceType.MAILBOX, "send_email", {"to": MANY}, True),
        (InstanceType.GMAIL, "send_email", {"to": ["a@b.example"]}, False),
        (InstanceType.GMAIL, "send_email", {"to": MANY}, True),
        (InstanceType.GMAIL, "send_email", {"to": MANY[:5], "cc": MANY[5:]}, True),
        (InstanceType.GOOGLE, "send_mail", {"to": ["a@b.example"]}, False),
        (InstanceType.GOOGLE, "send_mail", {"to": MANY}, True),
        (InstanceType.GOOGLE, "create_draft", {"to": MANY}, False),
        (InstanceType.MAILBOX, "create_draft", {"to": MANY}, False),
        (InstanceType.MAILBOX, "move_message", {}, False),
        (InstanceType.MAILBOX, "delete_message", {}, True),
        (InstanceType.HTTP, "send_request", {"method": "POST"}, False),
        (InstanceType.HTTP, "send_request", {"method": "DELETE"}, True),
        (InstanceType.S3, "upload_file", {}, False),
        (InstanceType.S3, "delete_file", {}, True),
    ],
)
def test_what_needs_the_name_typed(
    service: InstanceType, tool: str, args: dict[str, Any], typed: bool
) -> None:
    assert (destructive.reason(service, tool, args) is not None) is typed


# --- Holds, end to end ---


async def test_a_delete_asks_on_test_data_and_needs_the_name(
    linked: None, vault: Vault, http: FakeHttp
) -> None:
    held = await run_call(
        call(InstanceType.HTTP, "send_request", "acme-api", method="DELETE", path="/forms/1"), vault
    )
    assert held["status"] == "held_for_approval"
    assert not http.requests
    with pytest.raises(RuleViolationError, match="type the connection's name"):
        await approve(held["decision_id"], vault=vault)
    http.routes[("DELETE", "/v1/forms/1")] = {"ok": True}
    held = await run_call(
        call(InstanceType.HTTP, "send_request", "acme-api", method="DELETE", path="/forms/1"), vault
    )
    decision = await approve(held["decision_id"], vault=vault, confirm="acme-api")
    assert decision.status is DecisionStatus.EXECUTED


async def test_what_needs_the_name_cant_be_trusted_or_answered_from_chat(
    linked: None, vault: Vault
) -> None:
    held = await run_call(
        call(InstanceType.S3, "delete_file", "acme-files", key="a.txt", bucket="acme-files"), vault
    )
    with session_scope() as session:
        decision = decisions.get_decision(session, held["decision_id"])
        with pytest.raises(RuleViolationError, match="always asks"):
            trusts.grant(session, decision, by="owner")
        for via in ("telegram", "slack", "discord"):
            with pytest.raises(RuleViolationError, match="in the Ringkeeper app"):
                decisions.check_confirmation(decision, "acme-files", via)
        assert not inbox.quick_approval(decision)


async def test_a_per_tool_setting_or_preapproval_doesnt_let_it_run(
    vault: Vault, http: FakeHttp
) -> None:
    vault.store("file://acme/f", S3_SECRET)
    with session_scope() as session:
        found = make_instance(session, "acme-f", type=InstanceType.S3, secret_ref="file://acme/f")
        found.config = {"write_rules": {"delete_file": "run"}}
    bind("acme-web", "acme-f", preapproved_writes=["delete_file"])
    held = await run_call(
        call(InstanceType.S3, "delete_file", "acme-f", key="a.txt", bucket="acme-files"), vault
    )
    assert held["status"] == "held_for_approval"
    assert not http.requests


async def test_a_test_send_runs_and_goes_only_to_the_test_address(
    linked: None, vault: Vault, server: tuple[FakeServer, FakeSmtp]
) -> None:
    _, sender = server
    result = await run_call(
        call(InstanceType.SMTP, "send_test_email", "acme-smtp", subject="Hi", body="Test"), vault
    )
    assert result["to"] == ["owner@acme.example"]
    assert sender.sent[-1]["To"] == "owner@acme.example"
    assert sender.sent[-1]["Subject"] == "[Test] Hi"
    with session_scope() as session:
        rows = session.query(AuditEvent).all()
    assert rows[-1].outcome is AuditOutcome.EXECUTED


async def test_a_held_send_runs_once_approved(
    linked: None, vault: Vault, server: tuple[FakeServer, FakeSmtp]
) -> None:
    _, sender = server
    held = await run_call(
        call(
            InstanceType.SMTP, "send_email", "acme-smtp", to=["anna@acme.example"], subject="Hi",
            body="Hello",
        ),
        vault,
    )  # fmt: skip
    assert held["status"] == "held_for_approval"
    assert not sender.sent
    decision = await approve(held["decision_id"], vault=vault)
    assert decision.status is DecisionStatus.EXECUTED
    assert len(sender.sent) == 1


async def test_a_card_number_is_refused_and_kept_out_of_the_audit(
    linked: None, vault: Vault, http: FakeHttp
) -> None:
    card = "4242 4242 4242 4242"
    with pytest.raises(RefusalError, match="never handles"):
        await run_call(
            call(
                InstanceType.HTTP, "send_request", "acme-api", method="POST", path="/pay",
                json_body={"note": card},
            ),
            vault,
        )  # fmt: skip
    assert not http.requests
    with session_scope() as session:
        row = session.query(AuditEvent).order_by(AuditEvent.at.desc()).first()
        assert row is not None
        assert row.outcome is AuditOutcome.REFUSED
        assert card not in json.dumps(row.args)
        assert sensitive.MASK in json.dumps(row.args)
        assert not session.query(Trust).count()


# --- SMTP ---


def test_smtp_must_be_encrypted_and_needs_its_parts() -> None:
    with pytest.raises(ToolError, match="encrypted"):
        smtp.settings(json.dumps({**json.loads(SMTP_SECRET), "security": "none"}))
    with pytest.raises(ToolError, match="From address"):
        smtp.settings(json.dumps({**json.loads(SMTP_SECRET), "from_address": ""}))
    assert smtp.secret_fragments(SMTP_SECRET) == ["smtp-password-fake"]


async def test_smtp_says_a_refused_login_plainly(server: tuple[FakeServer, FakeSmtp]) -> None:
    wrong = json.dumps({**json.loads(SMTP_SECRET), "password": "not-it"})
    with pytest.raises(ToolError, match="app password or SMTP key"):
        await smtp.probe(target(wrong))


async def test_without_a_test_address_a_test_send_says_so(
    server: tuple[FakeServer, FakeSmtp],
) -> None:
    secret = json.dumps({**json.loads(SMTP_SECRET), "test_address": ""})
    with pytest.raises(ToolError, match="no test address"):
        await smtp.send_test_email.handler(target(secret), subject="x", body="y")


# --- Resend ---


async def test_resend_sends_from_the_connections_address(http: FakeHttp) -> None:
    http.routes[("POST", "/emails")] = {"id": "abc"}
    result = await resend.send_email.handler(
        target(RESEND_SECRET), to=["anna@acme.example"], subject="Hi", body="Hello"
    )
    assert result["id"] == "abc"
    body = json.loads(http.requests[0].content)
    assert body["from"] == "hello@acme.example"
    assert http.requests[0].headers["authorization"] == "Bearer re_fake_key_123456"


async def test_a_send_only_resend_key_passes_the_test(http: FakeHttp) -> None:
    http.routes[("GET", "/domains")] = httpx2.Response(
        401, json={"message": "This API key is restricted to only send emails"}
    )
    assert "only send" in await resend.probe(target(RESEND_SECRET))


# --- Mailbox ---


def test_a_preset_fills_in_the_servers() -> None:
    fields = mailbox.settings(MAILBOX_SECRET)
    assert fields["imap_host"] == "imap.zoho.com"
    assert fields["smtp"]["host"] == "smtp.zoho.com"
    assert fields["password"] == "abcdefghijklmnop"


def test_a_custom_mailbox_needs_its_servers() -> None:
    fields = {"preset": "custom", "address": "a@b.example", "password": "p w"}
    with pytest.raises(ToolError, match="IMAP and SMTP servers"):
        mailbox.settings(json.dumps(fields))
    fields |= {"imap_host": "imap.b.example", "smtp_host": "smtp.b.example", "smtp_port": "587",
               "smtp_security": "starttls"}  # fmt: skip
    found = mailbox.settings(json.dumps(fields))
    assert found["smtp"]["port"] == 587
    assert found["password"] == "p w"


async def test_a_refused_outlook_login_explains_microsofts_change(
    server: tuple[FakeServer, FakeSmtp],
) -> None:
    secret = json.dumps({"preset": "outlook", "address": "a@b.example", "password": "wrong"})
    with pytest.raises(ToolError, match="turned password sign-in off"):
        await mailbox.probe(target(secret))


async def test_searching_quotes_terms_and_sends_other_alphabets_as_a_literal(
    server: tuple[FakeServer, FakeSmtp],
) -> None:
    imap, _ = server
    await mailbox.search_messages.handler(
        target(MAILBOX_SECRET), sender='a"b', since="2026-11-05", unread=True
    )
    search = next(c for c in imap.commands if c[0] == "SEARCH")
    assert search[1:4] == ("UNSEEN", "SINCE", "5-Nov-2026")
    assert '"a\\"b"' in search
    imap.commands.clear()
    await mailbox.search_messages.handler(target(MAILBOX_SECRET), subject="счёт")
    search = next(c for c in imap.commands if c[0] == "SEARCH")
    assert search[1:3] == ("CHARSET", "UTF-8")
    assert search[-1] == "счёт".encode()


async def test_reading_a_message_and_the_folders(server: tuple[FakeServer, FakeSmtp]) -> None:
    found = await mailbox.get_message.handler(target(MAILBOX_SECRET), uid=7)
    assert "invoice" in found["body"]
    folders = await mailbox.list_folders.handler(target(MAILBOX_SECRET))
    assert folders["trash"] == "Corbeille"
    assert folders["drafts"] == "[Gmail]/Brouillons"


async def test_delete_moves_to_the_trash(server: tuple[FakeServer, FakeSmtp]) -> None:
    imap, _ = server
    await mailbox.delete_message.handler(target(MAILBOX_SECRET), uid=7)
    assert imap.moved == [("7", '"Corbeille"')]


# --- Any HTTP API ---


@pytest.mark.parametrize(
    ("secret", "header", "query"),
    [
        ({"auth": "bearer", "token": "t0ken"}, ("authorization", "Bearer t0ken"), None),
        (
            {"auth": "header", "header_name": "X-Api-Key", "token": "t0ken"},
            ("x-api-key", "t0ken"),
            None,
        ),
        (
            {"auth": "basic", "username": "u", "password": "p"},
            ("authorization", "Basic dTpw"),
            None,
        ),
        ({"auth": "query", "query_name": "api_key", "token": "t0ken"}, None, ("api_key", "t0ken")),
    ],
)
async def test_each_way_of_signing_in(
    http: FakeHttp,
    secret: dict[str, str],
    header: tuple[str, str] | None,
    query: tuple[str, str] | None,
) -> None:
    http.routes[("GET", "/v1/forms")] = {"forms": []}
    found = await http_api.get.handler(
        target(json.dumps(secret), "https://api.acme.example/v1"), path="/forms", params={"page": 2}
    )
    assert found == {"status": 200, "json": {"forms": []}}
    sent = http.requests[0]
    assert sent.url.params["page"] == "2"
    if header:
        assert sent.headers[header[0]] == header[1]
    if query:
        assert sent.url.params[query[0]] == query[1]


@pytest.mark.parametrize(
    "path", ["/../admin", "/a/%2e%2e/b", "/a/%252e%252e/b", "//evil.example/x", "/x\\..\\y"]
)
def test_a_path_cant_leave_the_address(path: str) -> None:
    with pytest.raises(ToolError):
        http_api.inside(path)


async def test_a_redirect_isnt_followed(http: FakeHttp) -> None:
    http.routes[("GET", "/v1/moved")] = httpx2.Response(
        302, headers={"location": "https://evil.example/"}
    )
    found = await http_api.get.handler(
        target(HTTP_SECRET, "https://api.acme.example/v1"), path="/moved"
    )
    assert found["status"] == 302
    assert "not followed" in found["redirect"]
    assert len(http.requests) == 1


async def test_endpoints_come_from_the_openapi_file(http: FakeHttp) -> None:
    spec = {"paths": {"/forms": {"get": {"summary": "List forms"}, "post": {"summary": "Add"}},
                      "/users": {"get": {"summary": "List users"}}}}  # fmt: skip
    http.routes[("GET", "/openapi.json")] = spec
    secret = json.dumps(
        {**json.loads(HTTP_SECRET), "spec_url": "https://api.acme.example/openapi.json"}
    )
    found = await http_api.list_endpoints.handler(
        target(secret, "https://api.acme.example/v1"), search="forms"
    )
    assert found["endpoints"] == [
        {"method": "GET", "path": "/forms", "summary": "List forms"},
        {"method": "POST", "path": "/forms", "summary": "Add"},
    ]
    assert "authorization" not in http.requests[0].headers  # the file gets no key


def test_every_secret_piece_is_scrubbed() -> None:
    basic = json.dumps({"auth": "basic", "username": "u", "password": "pass-word"})
    assert http_api.secret_fragments(basic) == ["pass-word", "dTpwYXNzLXdvcmQ="]
    assert storage.secret_fragments(S3_SECRET) == ["s3-secret-fake-456"]
    assert storage.secret_fragments(BLOB_SECRET) == ["vercel_blob_rw_fake_789"]


def test_a_path_scope_holds_at_folder_boundaries() -> None:
    assert remote.under("/v1/forms", "/v1/forms/3")
    assert remote.under("/v1/forms/", "/v1/forms")
    assert not remote.under("/v1/forms", "/v1/forms-old")
    assert not remote.under("/v1/forms", "/v1/forms/../users")
    assert not remote.under("/v1/forms", "/v1/forms/%2e%2e/users")
    spec = remote.NATIVE["http"]
    with pytest.raises(remote.ScopeError, match="limited to"):
        remote.narrow(spec, {"path": "/v1/forms"}, "get", {"path": "/v1/users"}, ["path"])


def test_a_storage_scope_pins_the_bucket_and_folder() -> None:
    spec = remote.NATIVE["s3"]
    scope = {"bucket": "acme", "folder": "clients/acme/"}
    narrowed = remote.narrow(spec, scope, "list_files", {}, ["bucket", "prefix"])
    assert narrowed == {"bucket": "acme", "prefix": "clients/acme/"}
    with pytest.raises(remote.ScopeError):
        remote.narrow(spec, scope, "read_file", {"key": "other/x"}, ["bucket", "key"])
    with pytest.raises(remote.ScopeError, match="whole"):
        remote.narrow(spec, scope, "list_buckets", {}, [])
    assert remote.check_scope(spec, {"folder": "x/"}) == {"folder": "x/"}


# --- File storage ---


def test_the_signature_matches_awss_own_example() -> None:
    """AWS's documented GET Object example (Signature Version 4 > Examples)."""
    fields = {"access_key": "AKIAIOSFODNN7EXAMPLE", "region": "us-east-1",
              "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"}  # fmt: skip
    empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    headers = storage.signed_headers(
        fields, "GET", "https://examplebucket.s3.amazonaws.com/?max-keys=2&prefix=J"[:45],
        {"max-keys": "2", "prefix": "J"}, empty,
        datetime.datetime(2013, 5, 24, tzinfo=datetime.UTC),
    )  # fmt: skip
    assert headers["Authorization"].endswith(
        "Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"
    )


LISTING = """<?xml version="1.0"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Contents><Key>a.txt</Key><Size>5</Size><LastModified>2026-11-01</LastModified></Contents>
  <NextContinuationToken>more</NextContinuationToken>
</ListBucketResult>"""


async def test_listing_reading_and_uploading(http: FakeHttp) -> None:
    http.routes[("GET", "/acme")] = httpx2.Response(200, text=LISTING)
    http.routes[("GET", "/acme/a.txt")] = httpx2.Response(200, text="hello")
    http.routes[("PUT", "/acme/b.txt")] = httpx2.Response(200)
    files = await storage.list_files.handler(target(S3_SECRET), bucket="acme", prefix="a")
    assert files == {
        "files": [{"key": "a.txt", "size": 5, "modified": "2026-11-01"}],
        "next_after": "more",
    }
    found = await storage.read_file.handler(target(S3_SECRET), key="a.txt", bucket="acme")
    assert found["text"] == "hello"
    await storage.upload_file.handler(target(S3_SECRET), key="b.txt", content="hi", bucket="acme")
    sent = http.requests[-1]
    assert sent.content == b"hi"
    assert sent.headers["authorization"].startswith("AWS4-HMAC-SHA256")


def test_r2_uses_the_accounts_address() -> None:
    secret = {"provider": "r2", "account_id": "a" * 32, "access_key": "k", "secret_key": "s"}
    fields = storage.settings(json.dumps(secret))
    assert fields["endpoint"] == f"https://{'a' * 32}.r2.cloudflarestorage.com"
    assert fields["region"] == "auto"


async def test_vercel_blob(http: FakeHttp) -> None:
    blob = {"pathname": "a.txt", "size": 5, "uploadedAt": "2026-11-01",
            "url": "https://x.public.blob.vercel-storage.com/a.txt"}  # fmt: skip
    http.routes[("GET", "/api/blob")] = {"blobs": [blob], "cursor": None}
    http.routes[("POST", "/api/blob/delete")] = {}
    http.routes[("PUT", "/api/blob/")] = {"url": blob["url"]}
    found = await storage.list_files.handler(target(BLOB_SECRET))
    assert found["files"][0]["key"] == "a.txt"
    assert http.requests[0].headers["authorization"] == "Bearer vercel_blob_rw_fake_789"
    await storage.upload_file.handler(target(BLOB_SECRET), key="a.txt", content="hi")
    assert http.requests[-1].url.params["pathname"] == "a.txt"
    await storage.delete_file.handler(target(BLOB_SECRET), key="a.txt")
    assert json.loads(http.requests[-1].content) == {"urls": [blob["url"]]}


# --- What the form's secret has to be ---


@pytest.mark.parametrize(
    ("kind", "bad", "says"),
    [
        (InstanceType.SMTP, "{}", "server"),
        (InstanceType.RESEND, '{"api_key": "nope"}', "re_"),
        (InstanceType.MAILBOX, "{}", "address"),
        (InstanceType.HTTP, '{"auth": "bearer"}', "key or token"),
        (InstanceType.S3, "{}", "access key"),
    ],
)
def test_a_bad_secret_is_stopped_at_the_form(kind: InstanceType, bad: str, says: str) -> None:
    with pytest.raises(RuleViolationError, match=says):
        check_connection_secret(kind, bad)
