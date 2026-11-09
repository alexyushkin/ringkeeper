"""Notification channels: Telegram, and your own Slack app or Discord bot.

Each channel chooses what it gets (prefs), and each thing goes to it once
(outbox). Slack and Discord get a short summary with Approve / Deny / Open
buttons (cards); only the approver set up for the channel can press them.
Their tokens are in the vault, never in the database.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.channels.cards import test_card
from ringkeeper.channels.discord import DiscordApi
from ringkeeper.channels.outbox import Transport
from ringkeeper.channels.prefs import Prefs
from ringkeeper.channels.slack import SlackApi
from ringkeeper.config import Settings
from ringkeeper.errors import NotFoundError, RuleViolationError
from ringkeeper.models import Channel, Setting
from ringkeeper.services import admin_audit
from ringkeeper.vault import get_vault

KINDS = ("slack", "discord")
TELEGRAM_KEY = "channels.telegram"
TOKENS = {"slack": ("bot_token", "app_token"), "discord": ("bot_token",)}


def token_ref(settings: Settings, channel_id: uuid.UUID, name: str) -> str:
    return f"{settings.channel_secret_prefix.rstrip('/')}/{channel_id}/{name.replace('_', '-')}"


def connect(kind: str, settings: Settings, tokens: dict[str, str]) -> SlackApi | DiscordApi:
    """A client for these tokens (not stored)."""
    if kind == "slack":
        bot, app = tokens.get("bot_token", ""), tokens.get("app_token", "")
        if not bot.startswith("xoxb-"):
            raise RuleViolationError("The bot token starts with xoxb- (OAuth & Permissions).")
        if not app.startswith("xapp-"):
            raise RuleViolationError("The app-level token starts with xapp- (Basic Information).")
        return SlackApi(bot, app, settings.slack_api_url)
    if kind == "discord":
        token = tokens.get("bot_token", "")
        if len(token) < 50 or " " in token:
            raise RuleViolationError("Paste the bot token from the Bot page (Reset Token).")
        return DiscordApi(token, settings.discord_api_url)
    raise RuleViolationError(f"Channels are {', '.join(KINDS)}.")


def stored_tokens(settings: Settings, channel: Channel) -> dict[str, str]:
    vault = get_vault(settings)
    return {
        name: vault.resolve(token_ref(settings, channel.id, name)).reveal()
        for name in TOKENS[channel.kind]
    }


def transport(settings: Settings, channel: Channel) -> Transport:
    api = connect(channel.kind, settings, stored_tokens(settings, channel))
    api.target = str(channel.config.get("target") or "")
    if not api.target:
        raise RuleViolationError(f"{channel.name}: choose where it posts.")
    return api


def list_channels(session: Session) -> Sequence[Channel]:
    return session.scalars(select(Channel).order_by(Channel.created_at)).all()


def get_channel(session: Session, ident: str | uuid.UUID) -> Channel:
    try:
        channel = session.get(Channel, uuid.UUID(str(ident)))
    except ValueError:
        channel = None
    if channel is None:
        raise NotFoundError("That channel isn't here any more. Refresh the page.")
    return channel


CONVERSATION_ID = (
    "That's a conversation's ID. Your member ID starts with U: "
    "profile picture › Profile › ⋮ › Copy member ID."  # noqa: RUF001
)


def _target(kind: str, target: str) -> str:
    target = target.strip()
    if kind == "discord" and target.isdigit():
        return target  # a channel id; a DM is "user:<id>"
    if kind == "discord" and not (target.startswith("user:") and target[5:].isdigit()):
        raise RuleViolationError("Choose a channel, or a DM with your user ID.")
    if kind == "slack" and target[:1] == "D":  # the DM's own ID, not the person's
        raise RuleViolationError(CONVERSATION_ID)
    if kind == "slack" and target[:1] not in ("C", "G", "U", "W"):
        raise RuleViolationError("Choose a channel, or a DM with your member ID (U...).")
    return target


def _approver(kind: str, approver: str) -> str:
    approver = approver.strip()
    if kind == "discord" and not approver.isdigit():
        raise RuleViolationError("Your Discord user ID is a number (Copy User ID).")
    if kind == "slack" and approver[:1] in ("C", "D", "G"):  # a channel's or DM's ID
        raise RuleViolationError(CONVERSATION_ID)
    if kind == "slack" and not (approver[:1] in ("U", "W") and approver.isalnum()):
        raise RuleViolationError("Your Slack member ID starts with U (Profile > Copy member ID).")
    return approver


def create_channel(
    session: Session,
    settings: Settings,
    kind: str,
    *,
    name: str,
    tokens: dict[str, str],
    target: str,
    approver: str,
    prefs: dict[str, Any] | None = None,
) -> Channel:
    """Checks the tokens with Slack or Discord, stores them in the vault, and saves
    the channel. It sends only what happens from now on."""
    api = connect(kind, settings, tokens)
    try:
        about = api.check()
    finally:
        api.close()
    channel = Channel(
        id=uuid.uuid4(), kind=kind, name=name.strip() or kind.capitalize(), enabled=True,
        config={"target": _target(kind, target), "approver": _approver(kind, approver), **about},
        prefs=Prefs.load(prefs).dump(), since=datetime.now(UTC),
    )  # fmt: skip
    vault = get_vault(settings)
    for token in TOKENS[kind]:
        vault.store(token_ref(settings, channel.id, token), tokens[token].strip())
    session.add(channel)
    session.flush()
    admin_audit.record(
        session, f"channel.{kind}", f"added {kind} channel {channel.name}", by="owner"
    )
    return channel


def update_channel(
    session: Session,
    channel: Channel,
    *,
    name: str | None = None,
    enabled: bool | None = None,
    target: str | None = None,
    approver: str | None = None,
    prefs: dict[str, Any] | None = None,
    requests: bool | None = None,
) -> Channel:
    """`requests`: take DMs from the approver as new requests."""
    if name is not None and name.strip():
        channel.name = name.strip()
    config = dict(channel.config)
    if target is not None:
        config["target"] = _target(channel.kind, target)
    if approver is not None:
        config["approver"] = _approver(channel.kind, approver)
    if requests is not None and requests != bool(config.get("requests")):
        config["requests"] = requests
        admin_audit.record(
            session, f"channel.{channel.kind}",
            f"{'turned on' if requests else 'turned off'} new requests by DM on {channel.name}",
            by="owner",
        )  # fmt: skip
    channel.config = config
    if prefs is not None:
        channel.prefs = Prefs.load(prefs).dump()
    if enabled is not None and enabled != channel.enabled:
        channel.enabled = enabled
        if enabled:
            channel.since = datetime.now(UTC)  # nothing from while it was off
    return channel


def delete_channel(session: Session, settings: Settings, channel: Channel) -> None:
    vault = get_vault(settings)
    for token in TOKENS[channel.kind]:
        vault.delete(token_ref(settings, channel.id, token))
    admin_audit.record(
        session, f"channel.{channel.kind}", f"removed {channel.kind} channel {channel.name}",
        by="owner",
    )  # fmt: skip
    session.delete(channel)


def send_test(settings: Settings, channel: Channel) -> str:
    """Posts a test message with a button only the approver's press is accepted on."""
    api = transport(settings, channel)
    try:
        return api.post(test_card())
    finally:
        api.close()


def targets(settings: Settings, channel: Channel) -> list[dict[str, str]]:
    api = connect(channel.kind, settings, stored_tokens(settings, channel))
    try:
        return api.channels()
    finally:
        api.close()


# --- Telegram's prefs (the rest of its setup is in settings) ---


def telegram_state(session: Session) -> tuple[Prefs, datetime]:
    """Telegram's prefs, and since when it sends events (its first look, or when
    they were last turned on)."""
    row = session.get(Setting, TELEGRAM_KEY)
    if row is None:
        row = Setting(
            key=TELEGRAM_KEY,
            value={"prefs": Prefs().dump(), "since": datetime.now(UTC).isoformat()},
        )
        session.add(row)
        session.flush()
    return Prefs.load(row.value.get("prefs")), datetime.fromisoformat(str(row.value["since"]))


def set_telegram_prefs(session: Session, prefs: dict[str, Any]) -> Prefs:
    _old, since = telegram_state(session)
    loaded = Prefs.load(prefs)
    row = session.get_one(Setting, TELEGRAM_KEY)
    row.value = {"prefs": loaded.dump(), "since": since.isoformat()}
    return loaded
