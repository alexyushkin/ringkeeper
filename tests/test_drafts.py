"""Saving a draft reaches nobody: it runs without asking and is logged, unless the connection
holds drafts too. Taking it back deletes the draft, and the task's result says where it is."""

from __future__ import annotations

import email
import email.policy
import json
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ringkeeper import netpolicy
from ringkeeper.adapters import gmail, google, mailbox, smtp, vendor
from ringkeeper.adapters.base import Hold, Target
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import AuditEvent, AuditOutcome, Decision, Env, InstanceType
from ringkeeper.services import drafts, google_connections, instances, tasks
from ringkeeper.services.calls import run_call
from ringkeeper.vault import Secret, Vault, get_vault
from tests import test_web_api
from tests.fake_services import FakeImap, FakeSmtp
from tests.helpers import bind, make_instance, make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner
ACME = Caller("acme-web", "session-1", "token-1")
MAILBOX_SECRET = json.dumps({
    "preset": "zoho", "address": "me@acme.example", "password": "abcd efgh ijkl mnop",
    "test_address": "owner@acme.example",
})  # fmt: skip
DRAFT = {"to": ["anna@acme.example"], "subject": "Your invoice", "body": "Hi Anna"}


@dataclass
class DraftServer(FakeImap):
    """An IMAP server that keeps saved drafts, finds them by Message-ID and deletes them."""

    deleted: int = 0

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.selected = mailbox
        return "OK", [str(len(self.appended)).encode()]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        if command == "SEARCH" and args[:2] == ("HEADER", "Message-ID"):
            wanted = args[2].strip('"')
            found = [
                str(n).encode() for n, (_, _, raw) in enumerate(self.appended, 1)
                if email.message_from_bytes(raw, policy=email.policy.default)["Message-ID"]
                == wanted
            ]  # fmt: skip
            return "OK", [b" ".join(found)]
        if command == "STORE":
            self.deleted += 1
            return "OK", [b""]
        return super().uid(command, *args)

    def expunge(self) -> tuple[str, list[bytes]]:
        return "OK", [b""]


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> DraftServer:
    imap = DraftServer()

    def any_imap(_host: str, _port: int) -> DraftServer:
        return imap

    def any_smtp(*_args: object) -> FakeSmtp:
        return FakeSmtp(password="smtp-password-fake")

    def any_host(_host: str) -> None:
        return None

    monkeypatch.setattr(mailbox, "imap_factory", any_imap)
    monkeypatch.setattr(smtp, "smtp_factory", any_smtp)
    monkeypatch.setattr(netpolicy, "check_host", any_host)
    return imap


@pytest.fixture
def vault(database_url: str) -> Vault:
    with session_scope() as session:
        make_project(session, "acme-web")
    found = get_vault()
    found.store("file://acme/acme-mail", MAILBOX_SECRET)
    with session_scope() as session:
        make_instance(
            session, "acme-mail", type=InstanceType.MAILBOX, env=Env.PROD,
            secret_ref="file://acme/acme-mail",
        )  # fmt: skip
    bind("acme-web", "acme-mail")
    return found


def draft_call() -> ToolCall:
    return ToolCall(ACME, InstanceType.MAILBOX, "create_draft", "acme-mail", dict(DRAFT))


@pytest.mark.anyio
async def test_a_draft_runs_without_asking_even_on_production(
    vault: Vault, server: DraftServer
) -> None:
    result = await run_call(draft_call(), vault)
    assert result["saved"] == "draft"
    assert result["message_id"]
    [(folder, flags, _raw)] = server.appended
    assert (folder, flags) == ('"[Gmail]/Brouillons"', "(\\Draft)")
    with session_scope() as session:
        [row] = session.query(AuditEvent).all()
        assert (row.tool, row.outcome) == ("create_draft", AuditOutcome.EXECUTED)
        assert not session.query(Decision).count()


@pytest.mark.anyio
async def test_a_connection_can_hold_drafts_too(vault: Vault, server: DraftServer) -> None:
    with session_scope() as session:
        instances.get_instance(session, "acme-mail").config = {"hold_drafts": True}
    held = await run_call(draft_call(), vault)
    assert held["status"] == "held_for_approval"
    assert not server.appended


@pytest.mark.anyio
async def test_taking_a_draft_back_deletes_it(vault: Vault, server: DraftServer) -> None:
    target = Target("acme", Env.PROD, None, Secret("file://acme/acme-mail", MAILBOX_SECRET))
    undo = mailbox.create_draft.undo
    assert undo is not None
    assert undo.why_not(DRAFT) is None  # not "can't be undone"
    kept = await undo.before(target, DRAFT)
    result = await mailbox.create_draft.handler(target, **DRAFT)
    await undo.after(target, DRAFT, kept, result)
    assert undo.describe(kept) == "Deletes the draft it saved (Your invoice)."
    assert await undo.restore(target, kept) == {"deleted": 1}
    assert server.deleted == 1


def test_gmail_and_google_drafts_link_to_the_draft() -> None:
    assert gmail.draft_link("me@x.example", "<abc@x>") == (
        "https://mail.google.com/mail/?authuser=me%40x.example#search/rfc822msgid:abc%40x"
    )
    assert google.draft_link("me@x.example", "18c2") == (
        "https://mail.google.com/mail/?authuser=me%40x.example#drafts?compose=18c2"
    )
    for spec in (gmail.create_draft, google.create_draft, mailbox.create_draft):
        assert spec.hold is Hold.DRAFT
        assert spec.typed is not None
        assert spec.typed({}) is None  # never the name typed
        assert spec.undo is not None


def test_an_outlook_draft_runs_and_its_undo_deletes_it() -> None:
    draft_args = {"operation": "microsoft.create_draft", "body": {"subject": "Hi"}}
    assert call_hold(draft_args) is Hold.DRAFT
    assert call_hold({"operation": "microsoft.update_draft"}) is Hold.ALWAYS
    assert call_hold({"operation": "microsoft.send_mail"}) is Hold.ALWAYS
    undo = vendor.call_operation.undo
    assert undo is not None
    assert undo.why_not(draft_args) is None
    assert undo.describe({"remove": "microsoft.delete_message"}) == "Deletes the draft it saved."


def call_hold(args: dict[str, Any]) -> Hold:
    return vendor.call_operation.hold_for(args)


def test_a_tasks_result_links_the_drafts_it_saved(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "acme-web")
        task = tasks.create_task(session, get_settings(), project="acme-web", prompt="Draft it")
        [worker] = task.sessions
        made = {"saved": "draft", "subject": "Your invoice", "link": "https://mail.example/d/1"}
        outlook = {"link": "https://o.example/1", "subject": "Hello", "id": "m1"}
        session.add_all([
            AuditEvent(project="acme-web", session_id=str(worker.id), tool="create_draft",
                       outcome=AuditOutcome.EXECUTED, instance="acme-gmail",
                       result=json.dumps(made), args={}),
            AuditEvent(project="acme-web", session_id=str(worker.id), tool="call_operation",
                       outcome=AuditOutcome.EXECUTED, instance="acme-outlook",
                       result=json.dumps(outlook)[:-4],  # cut short, as a long result is
                       args={"operation": "microsoft.create_draft"}),
            AuditEvent(project="acme-web", session_id=str(worker.id), tool="create_draft",
                       outcome=AuditOutcome.EXECUTED, instance="acme-mail",
                       result=json.dumps({"saved": "draft", "subject": "Plain"}), args={}),
            AuditEvent(project="acme-web", session_id=str(worker.id), tool="call_operation",
                       outcome=AuditOutcome.EXECUTED, args={"operation": "microsoft.mark_message"}),
        ])  # fmt: skip
        session.flush()
        said = drafts.linked(session, task, "Saved the drafts. https://mail.example/d/1")
    assert said == (
        "Saved the drafts. https://mail.example/d/1\n\nDrafts saved:\n"
        "- [Hello](https://o.example/1)\n"
        "- Plain (in acme-mail's drafts folder)"
    )


def test_the_switch_is_on_the_connection(owner: TestClient) -> None:
    with session_scope() as session:
        make_instance(session, "acme-mail", type=InstanceType.MAILBOX, secret_ref="file://a/m")
    on = owner.patch("/connections/acme-mail", json={"hold_drafts": True})
    assert on.status_code == 200, on.text
    assert on.json()["config"]["hold_drafts"] is True
    off = owner.patch("/connections/acme-mail", json={"hold_drafts": False})
    assert off.json()["config"]["hold_drafts"] is False


def test_a_google_accounts_draft_rule_is_its_hold_drafts_setting(database_url: str) -> None:
    with session_scope() as session:
        found = make_instance(session, "my-google", type=InstanceType.GOOGLE)
        found.config = {"access": ["mail_drafts"]}
        rule = {t["name"]: t["rule"] for t in google_connections.tools(found)}
        assert rule["create_draft"] == "run"  # by default: it reaches nobody
        google_connections.set_write_rules(session, "my-google", {})  # "ask" for every write
        assert found.config["hold_drafts"] is True
        assert "create_draft" not in found.config["write_rules"]
        rule = {t["name"]: t["rule"] for t in google_connections.tools(found)}
        assert rule["create_draft"] == "ask"
        google_connections.set_write_rules(session, "my-google", {"create_draft": "run"})
        assert found.config["hold_drafts"] is False
