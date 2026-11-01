"""Slack and Discord channels against fake servers: a notification is sent once,
a button approves once and only for the approver, the risky ones open in
Ringkeeper, quiet hours and scope are respected, tokens stay in the vault, and
Telegram follows its prefs too."""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper import channels
from ringkeeper.api.app import create_app
from ringkeeper.channels import discord as discord_module
from ringkeeper.channels import links
from ringkeeper.channels import slack as slack_module
from ringkeeper.channels.cards import NOT_ALLOWED, TEST_ACTION
from ringkeeper.channels.discord import DiscordApi
from ringkeeper.channels.outbox import Runner
from ringkeeper.channels.slack import SlackApi
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.desktop import opener
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    Decision,
    DecisionKind,
    DecisionStatus,
    Env,
    InstanceType,
    Notification,
    TaskStatus,
)
from ringkeeper.services import tasks
from ringkeeper.telegram import api as telegram_api
from ringkeeper.telegram.api import BotApi
from ringkeeper.telegram.bridge import Bridge
from ringkeeper.vault import get_vault
from tests.fake_chat import (
    DISCORD_API,
    DISCORD_BOT,
    SLACK_API,
    SLACK_APP,
    SLACK_BOT,
    FakeDiscord,
    FakeSlack,
    listening,
)
from tests.fake_telegram import BOT_TOKEN, ME, FakeTelegram
from tests.helpers import make_instance

pytestmark = pytest.mark.usefixtures("task_registry")

ME_SLACK, OTHER_SLACK = "U0ME", "U0OTHER"
ME_DISCORD, OTHER_DISCORD = "111222333", "444555666"
SQL = "UPDATE customers SET plan = 'gold' WHERE email = 'vip@acme.test'"


@pytest.fixture(autouse=True)
def _apis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_SLACK_API_URL", SLACK_API)
    monkeypatch.setenv("RINGKEEPER_DISCORD_API_URL", DISCORD_API)
    monkeypatch.setenv("RINGKEEPER_WEB_ORIGIN", "https://rk.example")


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSlack]:
    fake = FakeSlack()
    monkeypatch.setattr(slack_module, "transport", httpx2.MockTransport(fake.handle))
    yield fake
    fake.socket.close()


@pytest.fixture
def discord(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeDiscord]:
    fake = FakeDiscord()
    monkeypatch.setattr(discord_module, "transport", httpx2.MockTransport(fake.handle))
    yield fake
    fake.socket.close()


def add_slack(target: str = "C1", **prefs: Any) -> str:
    with session_scope() as session:
        made = channels.create_channel(
            session, get_settings(), "slack", name="Ops", target=target, approver=ME_SLACK,
            tokens={"bot_token": SLACK_BOT, "app_token": SLACK_APP}, prefs=prefs or None,
        )  # fmt: skip
        return str(made.id)


def add_discord(target: str = "501", **prefs: Any) -> str:
    with session_scope() as session:
        made = channels.create_channel(
            session, get_settings(), "discord", name="Ops", target=target,
            approver=ME_DISCORD, tokens={"bot_token": DISCORD_BOT}, prefs=prefs or None,
        )  # fmt: skip
        return str(made.id)


def runner(ident: str) -> Runner:
    settings = get_settings()
    with session_scope(commit=False) as session:
        channel = channels.get_channel(session, ident)
        return Runner(channel.id, settings, channels.transport(settings, channel))


def held_write(instance: str = "acme-db", **payload: Any) -> str:
    with session_scope() as session:
        decision = Decision(
            kind=DecisionKind.APPROVE, project="acme-web", session_id="s", token_id="t",
            instance=instance, service=InstanceType.SUPABASE, tool="execute",
            args={"sql": SQL}, args_hash="h", payload=payload or None,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )  # fmt: skip
        session.add(decision)
        session.flush()
        return str(decision.id)


def prod_connection() -> str:
    with session_scope() as session:
        make_instance(session, "acme-db-prod", env=Env.PROD)
    return held_write("acme-db-prod")


def status(ident: str) -> DecisionStatus:
    with session_scope(commit=False) as session:
        decision = session.get_one(Decision, ident)
        return DecisionStatus.APPROVED if decision.answer else decision.status


def ended_task(project: str, state: TaskStatus, summary: str = "Done.") -> str:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project=project, prompt="do it")
        task.status, task.finished_at = state, datetime.now(UTC)
        task.summary, task.error = (
            summary,
            "It broke." if state is not TaskStatus.SUCCEEDED else None,
        )
        return str(task.id)


def buttons(message: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        e for block in message["blocks"] if block["type"] == "actions" for e in block["elements"]
    ]


# --- Setup ---


def test_slack_tokens_are_checked_and_kept_in_the_vault_only(slack: FakeSlack) -> None:
    with pytest.raises(RuleViolationError, match="xoxb-"):
        channels.connect("slack", get_settings(), {"bot_token": "nope", "app_token": SLACK_APP})
    ident = add_slack()
    assert ("auth.test", {}) in slack.calls  # checked with Slack first
    settings = get_settings()
    vault = get_vault(settings)
    stored = {
        name: vault.resolve(channels.token_ref(settings, uuid.UUID(ident), name))
        for name in ("bot_token", "app_token")
    }
    assert {k: v.reveal() for k, v in stored.items()} == {
        "bot_token": SLACK_BOT, "app_token": SLACK_APP,
    }  # fmt: skip
    with session_scope(commit=False) as session:
        row = channels.get_channel(session, ident)
        saved = json.dumps([row.config, row.prefs])
    assert SLACK_BOT not in saved
    assert SLACK_APP not in saved
    assert json.loads(saved)[0] == {
        "target": "C1", "approver": ME_SLACK, "team": "Acme Inc", "bot": "ringkeeper",
    }  # fmt: skip


def test_slack_needs_both_tokens_to_work(slack: FakeSlack) -> None:
    with session_scope() as session, pytest.raises(Exception, match="invalid_auth"):
        channels.create_channel(
            session, get_settings(), "slack", name="Ops", target="C1", approver=ME_SLACK,
            tokens={"bot_token": SLACK_BOT, "app_token": "xapp-wrong-token-value"},
        )  # fmt: skip
    with session_scope(commit=False) as session:
        assert channels.list_channels(session) == []


# --- Slack ---


def test_slack_gets_a_short_summary_with_approve_deny_open_once(slack: FakeSlack) -> None:
    ident = add_slack()
    held = held_write()
    rk = runner(ident)
    assert rk.send_due() == 1
    assert rk.send_due() == 0  # once
    [message] = slack.posted()
    assert message["channel"] == "C1"
    assert message["text"].startswith("Approval · acme-web · acme-db\n")
    assert "UPDATE" not in message["text"]
    assert "vip@acme.test" not in message["text"]
    assert [(b["text"]["text"], b.get("value"), b.get("url")) for b in buttons(message)] == [
        ("Approve", f"rk:{held}:0", None),
        ("Deny", f"rk:{held}:1", None),
        ("Open in Ringkeeper", None, "https://rk.example/approvals"),
    ]


def test_slack_button_approves_once_and_only_for_the_approver(slack: FakeSlack) -> None:
    ident = add_slack()
    held = held_write()
    rk = runner(ident)
    rk.send_due()
    api = rk.transport
    assert isinstance(api, SlackApi)
    with listening(api, rk.on_press):
        reply = slack.press(f"rk:{held}:0", OTHER_SLACK)
        assert slack.replies[reply]["text"] == NOT_ALLOWED
        assert slack.replies[reply]["response_type"] == "ephemeral"
        assert status(held) is DecisionStatus.PENDING

        reply = slack.press(f"rk:{held}:0", ME_SLACK)
        assert slack.replies[reply]["text"].startswith("Done: approve")
        assert status(held) is DecisionStatus.APPROVED

        reply = slack.press(f"rk:{held}:0", ME_SLACK)
        assert slack.replies[reply]["text"].startswith("Not done")
    with session_scope(commit=False) as session:
        decision = session.get_one(Decision, held)
        assert decision.answered_via == "slack"
        assert (decision.decided_by or decision.answer or {}) not in (None, {})
    # The message now says what happened, with only Open left.
    update = slack.updated()[-1]
    assert [b["text"]["text"] for b in buttons(update)] == ["Open in Ringkeeper"]


def test_production_changes_and_deletes_open_in_ringkeeper(slack: FakeSlack) -> None:
    ident = add_slack()
    prod = prod_connection()
    delete = held_write(destructive=True)
    rk = runner(ident)
    assert rk.send_due() == 2
    for message in slack.posted():
        labels = [b["text"]["text"] for b in buttons(message)]
        assert labels == ["Deny", "Open in Ringkeeper"]
        assert "Approve it in Ringkeeper" in message["text"]
    api = rk.transport
    assert isinstance(api, SlackApi)
    with listening(api, rk.on_press):
        for held in (prod, delete):  # a made-up Approve press gets nowhere
            reply = slack.press(f"rk:{held}:0", ME_SLACK)
            assert slack.replies[reply]["text"] == "Approve this one in Ringkeeper."
            assert status(held) is DecisionStatus.PENDING


def test_quiet_hours_hold_messages_until_they_end(slack: FakeSlack) -> None:
    now = datetime.now(UTC)
    quiet = {
        "from": (now - timedelta(hours=1)).strftime("%H:%M"),
        "to": (now + timedelta(hours=1)).strftime("%H:%M"),
        "tz": "UTC",
    }
    ident = add_slack(quiet=quiet)
    held_write()
    rk = runner(ident)
    assert rk.send_due(now) == 0
    assert slack.posted() == []
    assert rk.send_due(now + timedelta(hours=2)) == 1


def test_scope_and_events_choose_what_is_sent(slack: FakeSlack) -> None:
    ident = add_slack(events=["approvals", "failures"], projects=["globex-portal"])
    skipped = held_write()  # acme-web: not chosen
    failed = ended_task("globex-portal", TaskStatus.FAILED)
    ended_task("globex-portal", TaskStatus.SUCCEEDED)  # finished: not chosen
    rk = runner(ident)
    assert rk.send_due() == 1
    [message] = slack.posted()
    assert message["text"].startswith(f"✖ globex-portal · task {failed[:8]} failed")
    assert buttons(message)[0]["url"] == f"https://rk.example/tasks/{failed}"
    with session_scope(commit=False) as session:
        note = session.scalars(
            select(Notification).where(Notification.decision_id == skipped)
        ).one()
        assert note.status == "skipped"  # and never looked at again
    assert rk.send_due() == 0


def test_a_dm_goes_to_the_approvers_direct_messages(slack: FakeSlack) -> None:
    ident = add_slack(target=ME_SLACK)
    held_write()
    assert runner(ident).send_due() == 1
    assert ("conversations.open", {"users": ME_SLACK}) in slack.calls
    assert slack.posted()[0]["channel"] == "D1"


def test_send_test_checks_whose_answers_count(slack: FakeSlack) -> None:
    ident = add_slack()
    with session_scope(commit=False) as session:
        channels.send_test(get_settings(), channels.get_channel(session, ident))
    [message] = slack.posted()
    assert buttons(message)[0]["value"] == TEST_ACTION
    rk = runner(ident)
    api = rk.transport
    assert isinstance(api, SlackApi)
    with listening(api, rk.on_press):
        assert slack.replies[slack.press(TEST_ACTION, ME_SLACK)]["text"].startswith("That's you")
        assert slack.replies[slack.press(TEST_ACTION, OTHER_SLACK)]["text"] == NOT_ALLOWED


# --- Discord ---


def test_discord_setup_gives_a_minimal_invite_link(discord: FakeDiscord) -> None:
    api = channels.connect("discord", get_settings(), {"bot_token": DISCORD_BOT})
    try:
        about = api.check()
        assert about["invite"] == (
            "https://discord.com/oauth2/authorize?client_id=800&scope=bot&permissions=3072"
        )
        assert api.channels() == [{"id": "501", "name": "Acme / #ops"}]
    finally:
        api.close()


def test_discord_button_approves_once_and_only_for_the_approver(discord: FakeDiscord) -> None:
    ident = add_discord()
    held = held_write()
    rk = runner(ident)
    assert rk.send_due() == 1
    [message] = discord.posted()
    assert "UPDATE" not in message["content"]
    assert message["allowed_mentions"] == {"parse": []}
    [row] = message["components"]
    assert [(b["label"], b["style"], b.get("custom_id")) for b in row["components"]] == [
        ("Approve", 3, f"rk:{held}:0"),
        ("Deny", 4, f"rk:{held}:1"),
        ("Open in Ringkeeper", 5, None),
    ]
    api = rk.transport
    assert isinstance(api, DiscordApi)
    with listening(api, rk.on_press):
        reply = discord.press(f"rk:{held}:0", OTHER_DISCORD)
        assert discord.callbacks[reply] == {
            "type": 4,
            "data": {"content": NOT_ALLOWED, "flags": 64},
        }
        assert status(held) is DecisionStatus.PENDING
        reply = discord.press(f"rk:{held}:0", ME_DISCORD)
        assert discord.callbacks[reply]["data"]["content"].startswith("Done: approve")
        assert status(held) is DecisionStatus.APPROVED
        assert discord.press(f"rk:{held}:0", ME_DISCORD) == reply + 1
        assert discord.callbacks[-1]["data"]["content"].startswith("Not done")
    identify = discord.identified()
    assert identify is not None
    assert identify["intents"] == 0
    [edit] = discord.edited()
    assert [b["label"] for b in edit["components"][0]["components"]] == ["Open in Ringkeeper"]


def test_discord_quiet_hours_and_dm(discord: FakeDiscord) -> None:
    now = datetime.now(UTC)
    quiet = {
        "from": (now - timedelta(hours=1)).strftime("%H:%M"),
        "to": (now + timedelta(hours=1)).strftime("%H:%M"),
        "tz": "UTC",
    }
    ident = add_discord(target=f"user:{ME_DISCORD}", quiet=quiet)
    held_write()
    rk = runner(ident)
    assert rk.send_due(now) == 0
    assert discord.posted() == []
    assert rk.send_due(now + timedelta(hours=2)) == 1
    assert ("POST", "users/@me/channels", {"recipient_id": ME_DISCORD}) in discord.calls
    assert ("POST", "channels/701/messages") in [(m, p) for m, p, _b in discord.calls]


# --- Telegram follows its prefs ---


def test_telegram_follows_its_prefs(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeTelegram()
    monkeypatch.setattr(telegram_api, "transport", httpx2.MockTransport(fake.handle))
    monkeypatch.setenv("RINGKEEPER_TELEGRAM_CHAT_IDS", json.dumps([ME]))
    settings = get_settings()
    get_vault().store(settings.telegram_bot_token_ref, BOT_TOKEN)
    bridge = Bridge(BotApi(BOT_TOKEN, settings.telegram_api_url), settings)
    with session_scope() as session:
        channels.set_telegram_prefs(session, {"events": ["failures"]})
    held_write()
    failed = ended_task("acme-web", TaskStatus.FAILED)
    assert bridge.send_new() == 1
    [sent] = fake.sent
    assert sent["text"].startswith(f"✖ acme-web · task {failed[:8]} failed")
    assert bridge.send_new() == 0


# --- The desktop app's Open link ---


def test_the_desktop_open_link_is_signed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_MODE", "desktop")
    monkeypatch.setattr(opener, "_asked", None)  # and back to none after the test
    settings = get_settings()
    with session_scope() as session:
        url = links.open_url(session, settings, "/approvals")
    assert url is not None
    assert url.startswith(f"http://127.0.0.1:{settings.hooks_port}/open?to=%2Fapprovals&sig=")
    sig = url.rsplit("sig=", 1)[1]
    with TestClient(create_app()) as client:
        assert client.get("/open", params={"to": "/approvals", "sig": "0" * 32}).status_code == 403
        assert client.get("/open", params={"to": "/settings", "sig": sig}).status_code == 403
        assert opener.pending() is None
        response = client.get("/open", params={"to": "/approvals", "sig": sig})
        assert response.status_code == 200
    asked = opener.pending()
    assert asked is not None
    assert asked["path"] == "/approvals"


def test_no_open_button_without_an_address(
    slack: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RINGKEEPER_WEB_ORIGIN")
    ident = add_slack()
    ended_task("acme-web", TaskStatus.FAILED)
    assert runner(ident).send_due() == 1
    assert buttons(slack.posted()[0]) == []
