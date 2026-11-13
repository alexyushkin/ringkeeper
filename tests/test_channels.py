"""Slack and Discord channels against fake servers: a notification is sent once,
a button approves once and only for the approver, the risky ones open in
Ringkeeper, quiet hours and scope are respected, tokens stay in the vault, and
Telegram follows its prefs too."""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper import channels
from ringkeeper.api.app import create_app
from ringkeeper.channels import discord as discord_module
from ringkeeper.channels import links, news
from ringkeeper.channels import slack as slack_module
from ringkeeper.channels.cards import NOT_ALLOWED, TEST_ACTION
from ringkeeper.channels.discord import DiscordApi
from ringkeeper.channels.news import GOT_IT, LOOKING
from ringkeeper.channels.outbox import NOT_YOURS, REQUESTS_OFF, Runner
from ringkeeper.channels.prefs import Prefs
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
    Message,
    MessageRole,
    Notification,
    Project,
    TaskStatus,
)
from ringkeeper.router.service import route_next
from ringkeeper.services import inbox, tasks, threads
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
    wait_for,
)
from tests.fake_router import FakeModel
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
    with session_scope() as session, pytest.raises(Exception, match="didn't accept the bot token"):
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
    assert message["text"].startswith("Approval · Acme web · acme-db\n")
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
        assert slack.replies[reply]["text"] == "Approved."
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
    assert message["text"].startswith("✖ Globex portal: failed")
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
        assert discord.callbacks[reply]["data"]["content"] == "Approved."
        assert status(held) is DecisionStatus.APPROVED
        assert discord.press(f"rk:{held}:0", ME_DISCORD) == reply + 1
        assert discord.callbacks[-1]["data"]["content"].startswith("Not done")
    identify = discord.identified()
    assert identify is not None
    assert identify["intents"] == 1 << 12  # DMs; interactions need no intent
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


def test_a_snoozed_approval_waits_and_is_sent_again_when_it_comes_back(slack: FakeSlack) -> None:
    ident = add_slack()
    held = held_write()
    rk = runner(ident)
    assert rk.send_due() == 1
    with session_scope() as session:
        inbox.snooze(session, held, datetime.now(UTC) + timedelta(minutes=30), by="owner")
    assert rk.send_due() == 0
    with session_scope() as session:  # its time comes: a snooze is a minute or more
        for note in session.scalars(select(Notification).where(Notification.decision_id == held)):
            note.created_at = datetime.now(UTC) - timedelta(minutes=31)
        session.get_one(Decision, held).snoozed_until = datetime.now(UTC) - timedelta(seconds=1)
    assert rk.send_due() == 1  # back: a fresh message
    assert rk.send_due() == 0  # once
    assert len(slack.posted()) == 2
    fresh = held_write()
    with session_scope() as session:  # snoozed before it was ever sent: sent when it's back
        inbox.snooze(session, fresh, datetime.now(UTC) + timedelta(minutes=30), by="owner")
    assert rk.send_due() == 0


# --- New requests by DM (Slack and Discord) ---


def take_requests(ident: str, on: bool = True) -> None:
    with session_scope() as session:
        channels.update_channel(session, channels.get_channel(session, ident), requests=on)


def asked(source: str) -> list[tuple[str, str]]:
    """(body, author) of each request from there."""
    with session_scope(commit=False) as session:
        rows = session.scalars(
            select(Message).where(Message.source == source, Message.role == MessageRole.USER)
        ).all()
        return [(m.body, m.author) for m in rows]


def answer_it(source: str, reply: str = "The site is up.", body: str | None = None) -> None:
    with session_scope() as session:
        found = select(Message).where(Message.source == source)
        if body is not None:
            found = found.where(Message.body == body)
        message = session.scalars(found).one()
        threads.mark_routed(session, message, {"output": {"reply": reply}})


def starts(reply: str, said: list[str]) -> bool:
    """A first reply was sent (a new conversation's also says where to follow it)."""
    return any(s.startswith(reply) for s in said)


def dm_texts(slack: FakeSlack) -> list[str]:
    return [m["text"] for m in slack.posted() if m["channel"] == "D1"]


def test_the_slack_app_is_set_up_to_take_dms() -> None:
    assert "im:history" in slack_module.SCOPES
    assert slack_module.MANIFEST["settings"]["event_subscriptions"] == {
        "bot_events": ["message.im"]
    }
    assert slack_module.MANIFEST["features"]["app_home"]["messages_tab_enabled"] is True


def test_a_slack_dm_from_the_approver_is_a_request_and_its_news_comes_back(
    slack: FakeSlack,
) -> None:
    ident = add_slack()
    take_requests(ident)
    rk = runner(ident)
    api = rk.transport
    assert isinstance(api, SlackApi)
    with listening(api, rk.on_press, rk.on_message):
        slack.dm("Delete every customer", OTHER_SLACK)
        wait_for(lambda: NOT_YOURS in dm_texts(slack))
        slack.dm("I'm the bot", ME_SLACK, bot_id="B1")  # its own messages are never requests
        slack.dm("edited", ME_SLACK, subtype="message_changed")
        slack.dm("Is the Acme site up?", ME_SLACK)
        wait_for(lambda: starts(LOOKING, dm_texts(slack)))  # a question: maybe no project at all
        slack.dm("Add a line to acme-web's notes", ME_SLACK)
        wait_for(lambda: starts(GOT_IT, dm_texts(slack)))
    assert asked("slack") == [
        ("Is the Acme site up?", f"slack:{ME_SLACK}"),
        ("Add a line to acme-web's notes", f"slack:{ME_SLACK}"),
    ]
    answer_it("slack", body="Is the Acme site up?")
    assert rk.send_news() == 1
    assert dm_texts(slack)[-1] == "The site is up."
    assert rk.send_news() == 0  # once


def test_a_slack_dm_is_stored_without_slack_s_markup(slack: FakeSlack) -> None:
    ident = add_slack()
    take_requests(ident)
    rk = runner(ident)
    api = rk.transport
    assert isinstance(api, SlackApi)
    with listening(api, rk.on_press, rk.on_message):
        slack.dm(
            "Email <mailto:a@b.com|a@b.com> the <https://x.example|price list> &amp; notes",
            ME_SLACK,
        )
        wait_for(lambda: len(asked("slack")) == 1)
    assert asked("slack")[0][0] == "Email a@b.com the price list (https://x.example) & notes"


def test_a_discord_dm_is_stored_without_its_mention_codes(discord: FakeDiscord) -> None:
    ident = add_discord()
    take_requests(ident)
    rk = runner(ident)
    api = rk.transport
    assert isinstance(api, DiscordApi)
    with listening(api, rk.on_press, rk.on_message):
        discord.dm(
            "Ask <@123> about <:party:9>", ME_DISCORD,
            mentions=[{"id": "123", "username": "ana"}],
        )  # fmt: skip
        wait_for(lambda: len(asked("discord")) == 1)
    assert asked("discord")[0][0] == "Ask @ana about :party:"


def test_dm_requests_are_off_until_turned_on(slack: FakeSlack) -> None:
    ident = add_slack()
    rk = runner(ident)
    assert rk.on_message(ME_SLACK, "Is the site up?", f"slack:{ME_SLACK}") == REQUESTS_OFF
    assert asked("slack") == []
    take_requests(ident)
    said = runner(ident).on_message(ME_SLACK, "Is the site up?", f"slack:{ME_SLACK}")
    assert starts(LOOKING, [said or ""])
    assert len(asked("slack")) == 1


def test_a_discord_dm_from_the_approver_is_a_request(discord: FakeDiscord) -> None:
    ident = add_discord()
    take_requests(ident)
    rk = runner(ident)
    api = rk.transport
    assert isinstance(api, DiscordApi)

    def said() -> list[str]:
        return [m.get("content", "") for m in discord.posted()]

    with listening(api, rk.on_press, rk.on_message):
        discord.dm("Delete every customer", OTHER_DISCORD)
        wait_for(lambda: NOT_YOURS in said())
        discord.dm("In a server channel", ME_DISCORD, guild_id="601")  # not a DM
        discord.dm("A bot", ME_DISCORD, author={"id": ME_DISCORD, "bot": True})
        discord.dm("Is the Acme site up?", ME_DISCORD)
        wait_for(lambda: starts(LOOKING, said()))
    [(body, author)] = asked("discord")
    assert body == "Is the Acme site up?"
    assert author.startswith(f"discord:{ME_DISCORD}")
    answer_it("discord")
    assert rk.send_news() == 1
    assert ("POST", "users/@me/channels", {"recipient_id": ME_DISCORD}) in discord.calls
    assert said()[-1] == "The site is up."


SETUP_QUESTION = "how many connections and clients do we currently have?"
SETUP_ANSWER = "13 connections and 4 clients."


def answer_about_ringkeeper() -> None:
    """The router as in real use: about Ringkeeper, answered from its own records."""
    model = FakeModel([{"tasks": [], "about_ringkeeper": True}, SETUP_ANSWER])
    assert route_next(get_settings(), model) is not None


def test_a_question_about_ringkeeper_by_slack_dm_gets_the_answer(slack: FakeSlack) -> None:
    """From real use: the app answered "13 connections and 4 clients", the DM got
    "Your request: Nothing to do." It gets the app's answer, and isn't told a project
    is being found."""
    ident = add_slack()
    take_requests(ident)
    rk = runner(ident)
    assert starts(LOOKING, [rk.on_message(ME_SLACK, SETUP_QUESTION, f"slack:{ME_SLACK}") or ""])
    answer_about_ringkeeper()
    assert rk.send_news() == 1
    assert dm_texts(slack)[-1] == SETUP_ANSWER
    assert rk.send_news() == 0


def test_a_question_about_ringkeeper_by_discord_dm_gets_the_answer(
    discord: FakeDiscord,
) -> None:
    ident = add_discord()
    take_requests(ident)
    rk = runner(ident)
    said = rk.on_message(ME_DISCORD, SETUP_QUESTION, f"discord:{ME_DISCORD}")
    assert starts(LOOKING, [said or ""])
    answer_about_ringkeeper()
    assert rk.send_news() == 1
    assert [m.get("content", "") for m in discord.posted()][-1] == SETUP_ANSWER


@pytest.mark.parametrize("given", ["D0123ABCD", "C0123ABCD"])
def test_a_slack_conversation_id_is_not_a_member_id(slack: FakeSlack, given: str) -> None:
    with session_scope() as session, pytest.raises(RuleViolationError) as refused:
        channels.create_channel(
            session, get_settings(), "slack", name="Ops", target="C1", approver=given,
            tokens={"bot_token": SLACK_BOT, "app_token": SLACK_APP},
        )  # fmt: skip
    assert str(refused.value) == (
        "That's a conversation's ID. Your member ID starts with U: "
        "profile picture › Profile › ⋮ › Copy member ID."  # noqa: RUF001
    )


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
    ended_task("acme-web", TaskStatus.FAILED)
    assert bridge.send_new() == 1
    [sent] = fake.sent
    assert sent["text"].startswith("✖ Acme web: failed")
    assert bridge.send_new() == 0


# --- The desktop app's Open link ---


def test_the_desktop_open_link_is_signed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_MODE", "desktop")
    monkeypatch.setattr(opener, "_asked", None)  # and back to none after the test
    monkeypatch.setattr(opener, "_sign_in", None)  # no backend running to sign a browser in
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


def test_the_open_link_signs_this_browser_in_when_no_window_takes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ringkeeper.desktop import server  # noqa: PLC0415

    monkeypatch.setenv("RINGKEEPER_MODE", "desktop")
    monkeypatch.setattr(opener, "_asked", None)
    monkeypatch.setattr(opener, "_shell_seen", None)
    launch = server.Launch()

    def sign_in(page: str) -> str:
        return server.sign_in_address(launch, 8765, page)

    monkeypatch.setattr(opener, "_sign_in", sign_in)
    with session_scope() as session:
        url = links.open_url(session, get_settings(), "/approvals")
    assert url is not None
    sig = url.rsplit("sig=", 1)[1]
    with TestClient(create_app(), follow_redirects=False) as client:
        sent = client.get("/open", params={"to": "/approvals", "sig": sig})
        assert sent.status_code == 303
        there = sent.headers["location"]
        assert there.startswith("http://127.0.0.1:8765/desktop/start?code=")
        assert opener.pending() is None  # no window asked to open
        assert client.get("/open", params={"to": "/approvals", "sig": "0" * 32}).status_code == 403

        # The start code is real, once: it signs the browser in and lands it on Approvals.
        door = TestClient(
            server.FrontDoor(create_app(), launch, 8765, None),
            base_url="http://127.0.0.1:8765", follow_redirects=False,
        )  # fmt: skip
        signed_in = door.get(there.removeprefix("http://127.0.0.1:8765"))
        assert (signed_in.status_code, signed_in.headers["location"]) == (303, "/approvals")
        assert server.SESSION_COOKIE in signed_in.cookies
        assert door.get(there.removeprefix("http://127.0.0.1:8765")).status_code == 403

        # With the app's window there (the shell asked for its status), the window opens it.
        opener.shell_asked()
        opened = client.get("/open", params={"to": "/approvals", "sig": sig})
        assert opened.status_code == 200
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


# --- Chats work like a conversation ---


def chat_threads(source: str) -> list[str]:
    with session_scope(commit=False) as session:
        rows = session.scalars(
            select(Message)
            .where(Message.source == source, Message.role == MessageRole.USER)
            .order_by(Message.created_at)
        ).all()
        return [str(m.thread_id) for m in rows]


def age_chat(source: str, hours: float) -> None:
    """Every message from the chat, as if sent `hours` ago."""
    with session_scope() as session:
        for message in session.scalars(select(Message).where(Message.thread_id.in_(
            select(Message.thread_id).where(Message.source == source)
        ))):  # fmt: skip
            message.created_at = datetime.now(UTC) - timedelta(hours=hours)


@pytest.mark.parametrize("kind", ["slack", "discord"])
def test_dms_continue_one_conversation_until_quiet_or_new(
    kind: str, slack: FakeSlack, discord: FakeDiscord
) -> None:
    ident = add_slack() if kind == "slack" else add_discord()
    take_requests(ident)
    rk = runner(ident)
    me = ME_SLACK if kind == "slack" else ME_DISCORD
    first = rk.on_message(me, "Read Acme's newest email", f"{kind}:{me}")
    assert first is not None
    assert first.startswith(GOT_IT)
    assert "New conversation: https://rk.example/thread" in first  # where to follow it
    assert rk.on_message(me, "Now publish the result here", f"{kind}:{me}") == GOT_IT
    one, two = chat_threads(kind)
    assert one == two  # the follow-up has the first request as context
    age_chat(kind, 3)  # quiet for over 2 hours
    rk.on_message(me, "Read Globex's newest email", f"{kind}:{me}")
    assert len(set(chat_threads(kind))) == 2
    assert rk.on_message(me, "new", f"{kind}:{me}") == threads.STARTED_NEW
    rk.on_message(me, "Check the shop's orders", f"{kind}:{me}")
    assert len(set(chat_threads(kind))) == 3
    rk.on_message(me, "new: check the blog", f"{kind}:{me}")
    assert len(set(chat_threads(kind))) == 4


TABLE = (
    "Three open orders:\n\n| Order | Customer | Total |\n|---|---|---|\n"
    "| 1007 | Ana | $40 |\n| 1008 | Chloe | $12 |\n| 1009 | Ben | $7 |"
)


def test_a_dm_s_task_ends_with_its_result(slack: FakeSlack) -> None:
    ident = add_slack()
    take_requests(ident)
    rk = runner(ident)
    rk.on_message(ME_SLACK, "List the open orders", f"slack:{ME_SLACK}")
    answer_it("slack")
    with session_scope() as session:
        message = session.scalars(select(Message).where(Message.source == "slack")).one()
        task = tasks.create_task(
            session, get_settings(), project="acme-web", prompt="list", thread_id=message.thread_id,
            message_id=message.id,
        )  # fmt: skip
        task.status, task.finished_at, task.summary = (
            TaskStatus.SUCCEEDED, datetime.now(UTC), TABLE,
        )  # fmt: skip
    rk.send_news()
    said = dm_texts(slack)[-1]
    assert said.startswith("✔ Acme web: answered\nThree open orders:")
    assert "1007 · Ana · $40" in said  # a table reads as lines
    assert "|" not in said
    assert "---" not in said


def a_finished_task_with_files(source: str, folder: Path) -> None:
    """The DM's request ends with a task that wrote orders.csv (and a .env, never sent)."""
    (folder / "orders.csv").write_bytes(b"id,total\n1,40\n")
    (folder / ".env").write_text("SECRET=1\n", encoding="utf-8")
    with session_scope() as session:
        session.scalars(
            select(Project).where(Project.name == "acme-web")
        ).one().workspace_path = str(folder)
        message = session.scalars(select(Message).where(Message.source == source)).one()
        task = tasks.create_task(
            session, get_settings(), project="acme-web", prompt="export",
            thread_id=message.thread_id, message_id=message.id,
        )  # fmt: skip
        task.status, task.finished_at, task.summary = (
            TaskStatus.SUCCEEDED, datetime.now(UTC), "Exported 1 order.",
        )  # fmt: skip
        task.changed_files = ["orders.csv", ".env"]


@pytest.mark.parametrize("kind", ["slack", "discord"])
def test_a_dm_s_task_result_comes_with_its_files(
    kind: str, slack: FakeSlack, discord: FakeDiscord, tmp_path: Path
) -> None:
    ident = add_slack() if kind == "slack" else add_discord()
    take_requests(ident)
    rk = runner(ident)
    me = ME_SLACK if kind == "slack" else ME_DISCORD
    rk.on_message(me, "Export the orders", f"{kind}:{me}")
    answer_it(kind)
    a_finished_task_with_files(kind, tmp_path)
    rk.send_news()
    if kind == "slack":
        assert slack.shared() == [{"channel": "D1", "titles": ["orders.csv"]}]
        assert list(slack.uploads.values()) == [b"id,total\n1,40\n"]
    else:
        [(path, names, raw)] = discord.attached
        assert (path, names) == ("channels/701/messages", ["orders.csv"])
        assert b"id,total" in raw
        assert b"SECRET" not in raw


@pytest.mark.parametrize("kind", ["slack", "discord"])
def test_full_results_off_sends_only_how_it_ended(
    kind: str, slack: FakeSlack, discord: FakeDiscord, tmp_path: Path
) -> None:
    ident = add_slack(full_results=False) if kind == "slack" else add_discord(full_results=False)
    take_requests(ident)
    rk = runner(ident)
    me = ME_SLACK if kind == "slack" else ME_DISCORD
    rk.on_message(me, "Export the orders", f"{kind}:{me}")
    answer_it(kind)
    a_finished_task_with_files(kind, tmp_path)
    rk.send_news()
    if kind == "slack":
        said = dm_texts(slack)[-1]
        assert said.startswith("✔ Acme web: done")
        assert "Exported 1 order." not in said
        assert slack.shared() == []
    else:
        assert discord.attached == []
    with session_scope(commit=False) as session:
        assert channels.get_channel(session, ident).prefs["full_results"] is False


def test_full_results_are_on_unless_turned_off() -> None:
    assert Prefs.load(None).full_results
    assert Prefs.load({"events": ["approvals"]}).full_results  # saved before the setting
    off = Prefs.load({"full_results": False})
    assert not off.full_results
    assert Prefs.load(off.dump()) == off
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="acme-web", prompt="list")
        task.status, task.summary = TaskStatus.SUCCEEDED, "Three open orders."
        said = news.result_text(task, "https://rk.example/task?id=1", full=False)
    assert said == "✔ Acme web: answered\nhttps://rk.example/task?id=1"


def test_a_long_result_ends_with_a_link() -> None:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="acme-web", prompt="list")
        task.status, task.summary = TaskStatus.SUCCEEDED, "A long finding. " * 300
        said = news.result_text(task, "https://rk.example/task?id=1")
    assert len(said) < 1700
    assert said.endswith("The rest is in Ringkeeper: https://rk.example/task?id=1")


def test_an_approval_from_a_dm_task_goes_to_that_dm(slack: FakeSlack) -> None:
    ident = add_slack(events=["failures"])  # the channel itself doesn't take approvals
    take_requests(ident)
    rk = runner(ident)
    rk.on_message(ME_SLACK, "Mark order 1007 shipped", f"slack:{ME_SLACK}")
    held = held_write()
    with session_scope() as session:
        message = session.scalars(select(Message).where(Message.source == "slack")).one()
        task = tasks.create_task(
            session, get_settings(), project="acme-web", prompt="ship",
            thread_id=message.thread_id, message_id=message.id,
        )  # fmt: skip
        session.get_one(Decision, uuid.UUID(held)).task_id = task.id
    other = held_write()  # not from a chat: the channel's prefs leave it out
    assert rk.send_due() == 1
    [card] = [m for m in slack.posted() if "Approval" in m["text"]]
    assert card["channel"] == "D1"  # the DM, with its buttons
    assert any(b.get("value") == f"rk:{held}:0" for b in buttons(card))
    assert other not in str(slack.posted())


# --- Wording in chats ---


def route_question(slack: FakeSlack) -> tuple[Runner, str]:
    ident = add_slack()
    take_requests(ident)
    rk = runner(ident)
    rk.on_message(ME_SLACK, "Fix the contact form", f"slack:{ME_SLACK}")
    with session_scope() as session:
        message = session.scalars(select(Message).where(Message.source == "slack")).one()
        decision = inbox.create(
            session, get_settings(), DecisionKind.ROUTE,
            {
                "message": message.body, "reason": "Both Website and Blog have a contact form.",
                "candidates": [{"project": "acme-web", "confidence": 0.5}],
                "task": {"outcome": "INTERNAL OUTCOME TEXT", "prompt": "fix it"},
            },
            thread_id=message.thread_id, message_id=message.id,
        )  # fmt: skip
        threads.mark_routed(session, message, {"tasks": [], "decisions": [str(decision.id)]})
        return rk, str(decision.id)


def test_which_project_is_asked_once_in_plain_words(slack: FakeSlack) -> None:
    rk, ident = route_question(slack)
    assert rk.send_news() == 0  # no "I need to know which project" line
    assert rk.send_news() == 0  # and it isn't tried again
    rk.send_due()
    [card] = [m for m in slack.posted() if m["text"].startswith("Which project?")]
    assert card["text"] == "Which project?\nBoth Website and Blog have a contact form."
    assert "INTERNAL" not in str(card)
    labels = [b["text"]["text"] for b in buttons(card)]
    assert labels[:2] == ["Acme web", "None of these"]  # names, not slugs
    with session_scope() as session:
        decision = session.get_one(Decision, uuid.UUID(ident))
        assert inbox.pressed(decision, "acme-web") == "OK: Acme web."
        assert inbox.pressed(decision, inbox.NO_PROJECT) == "OK, nothing was started."


def test_a_decided_card_shows_how_it_ended(slack: FakeSlack) -> None:
    ident = add_slack()
    held = prod_connection()
    rk = runner(ident)
    rk.send_due()
    with session_scope() as session:
        row = session.get_one(Decision, uuid.UUID(held))
        row.status, row.decided_by = DecisionStatus.EXECUTED, f"slack:{ME_SLACK} (alex)"
        row.decided_at = datetime.now(UTC)
    rk.close_finished()
    text = slack.updated()[-1]["text"]
    assert text.endswith("\n✔ Approved by alex (Slack). It ran.")
    assert "Approve it in Ringkeeper" not in text  # the open card's instructions are gone
