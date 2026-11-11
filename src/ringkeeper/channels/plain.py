"""A chat message as the person wrote it, without the chat's own markup, before it's stored
and reaches the router and the task.

Slack sends links and mentions in angle brackets ("<mailto:a@b.com|a@b.com>",
"<https://x.example|the doc>", "<@U123>") and escapes &, < and >. Discord sends mentions as
"<@123>" and custom emoji as "<:name:123>". Telegram sends plain text, with a link's address
in an entity rather than in the text."""

import html
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

_SLACK = re.compile(r"<([^<>|]+)(?:\|([^<>]*))?>")
_DISCORD_USER = re.compile(r"<@!?(\d+)>")
_DISCORD_CHANNEL = re.compile(r"<#(\d+)>")
_DISCORD_ROLE = re.compile(r"<@&(\d+)>")
_DISCORD_EMOJI = re.compile(r"<a?:(\w+):\d+>")
_DISCORD_TIME = re.compile(r"<t:(-?\d+)(?::[tTdDfFR])?>")


def _bare(url: str) -> str:
    return re.sub(r"^(?:https?://|mailto:)", "", url).rstrip("/")


def _link(target: str, label: str | None) -> str:
    """A link as words: its label, and its address when the label isn't the address."""
    if target.startswith("mailto:"):
        address = target.removeprefix("mailto:")
        return address if not label or label == address else f"{label} ({address})"
    if not label or _bare(label) == _bare(target):
        return target
    return f"{label} ({target})"


def from_slack(text: str) -> str:
    def one(match: re.Match[str]) -> str:
        target, label = match.group(1), match.group(2)
        if target.startswith(("@U", "@W")):  # a person: their name if Slack sent it
            return f"@{label or target[1:]}"
        if target.startswith("#C"):  # a channel
            return f"#{label or target[1:]}"
        if target.startswith("!"):  # @here, @channel, a user group
            name = target[1:].split("^")[0]
            return f"@{label or name}"
        return _link(target, label)

    return html.unescape(_SLACK.sub(one, text))


def from_discord(text: str, mentions: Sequence[dict[str, Any]] = ()) -> str:
    names = {
        str(m.get("id")): str(m.get("global_name") or m.get("username") or "") for m in mentions
    }

    def when(match: re.Match[str]) -> str:
        try:
            return datetime.fromtimestamp(int(match.group(1)), UTC).strftime("%Y-%m-%d %H:%M UTC")
        except (OverflowError, ValueError, OSError):
            return match.group(0)

    text = _DISCORD_USER.sub(lambda m: f"@{names.get(m.group(1)) or 'someone'}", text)
    text = _DISCORD_ROLE.sub("@role", text)
    text = _DISCORD_CHANNEL.sub("#channel", text)
    text = _DISCORD_EMOJI.sub(lambda m: f":{m.group(1)}:", text)
    return _DISCORD_TIME.sub(when, text)


def from_telegram(text: str, entities: Sequence[dict[str, Any]] = ()) -> str:
    """Telegram's text is plain; a link written over words keeps its address in an entity,
    added after the words. Offsets count UTF-16 code units, as Telegram does."""
    links = sorted(
        (e for e in entities if e.get("type") == "text_link" and e.get("url")),
        key=lambda e: int(e.get("offset", 0)),
        reverse=True,
    )
    if not links:
        return text
    units = text.encode("utf-16-le")
    for entity in links:  # from the end, so earlier offsets still hold
        end = (int(entity.get("offset", 0)) + int(entity.get("length", 0))) * 2
        label = units[int(entity.get("offset", 0)) * 2 : end].decode("utf-16-le", "replace")
        if _bare(label) == _bare(str(entity["url"])):
            continue
        units = units[:end] + f" ({entity['url']})".encode("utf-16-le") + units[end:]
    return units.decode("utf-16-le", "replace")
