"""A request from a chat reaches the router and the task as the person wrote it: without
Slack's link and mention markup and its &amp; escapes, Discord's mention codes, or a
Telegram link's address hidden in an entity."""

import pytest

from ringkeeper.channels.plain import from_discord, from_slack, from_telegram


@pytest.mark.parametrize(
    ("sent", "written"),
    [
        ("Email <mailto:a@b.com|a@b.com> the invoice", "Email a@b.com the invoice"),
        ("Email <mailto:a@b.com> today", "Email a@b.com today"),
        ("Email <mailto:a@b.com|Ana> today", "Email Ana (a@b.com) today"),
        ("Check <https://x.example|the doc>", "Check the doc (https://x.example)"),
        ("Check <https://x.example/a|x.example/a>", "Check https://x.example/a"),
        ("Check <https://x.example>", "Check https://x.example"),
        ("Ask <@U0123|ana> in <#C0456|ops>", "Ask @ana in #ops"),
        ("Ask <@U0123>", "Ask @U0123"),
        ("Tell <!here>", "Tell @here"),
        ("Fish &amp; chips &lt;3 &gt; 2", "Fish & chips <3 > 2"),
        ("Plain words stay as they are", "Plain words stay as they are"),
    ],
)
def test_slack_markup_becomes_plain_text(sent: str, written: str) -> None:
    assert from_slack(sent) == written


def test_discord_mentions_become_names() -> None:
    mentions = [{"id": "123", "username": "ana", "global_name": "Ana"}]
    assert (
        from_discord("Ask <@123> and <@!123> in <#999>", mentions)
        == "Ask @Ana and @Ana in #channel"
    )
    assert from_discord("Ping <@&55> :) <:party:777>") == "Ping @role :) :party:"
    assert from_discord("By <t:1731272400:R>") == "By 2024-11-10 21:00 UTC"
    assert from_discord("Ask <@404>") == "Ask @someone"


def test_a_telegram_link_s_address_is_kept() -> None:
    text = "Read the doc and reply"
    entities = [{"type": "text_link", "offset": 9, "length": 3, "url": "https://x.example/d"}]
    assert from_telegram(text, entities) == "Read the doc (https://x.example/d) and reply"
    # Offsets count UTF-16 units: an emoji before the link is two of them.
    emoji = "🙂 see notes"
    linked = [{"type": "text_link", "offset": 7, "length": 5, "url": "https://x.example/n"}]
    assert from_telegram(emoji, linked) == "🙂 see notes (https://x.example/n)"
    plain_url = [{"type": "url", "offset": 0, "length": 9}]
    assert from_telegram("x.example", plain_url) == "x.example"
