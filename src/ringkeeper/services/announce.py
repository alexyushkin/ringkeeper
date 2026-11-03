"""A message for you outside the app: to every Slack and Discord channel that's on, and
to Telegram's chats. For things that aren't about one task or approval (a connection
that stopped working, the morning digest). A channel that can't be reached is logged
and skipped: this never stops what called it.
"""

import logging

from sqlalchemy.orm import Session

from ringkeeper.channels import list_channels, transport
from ringkeeper.channels.cards import Card
from ringkeeper.channels.prefs import Prefs
from ringkeeper.config import Settings
from ringkeeper.errors import RingkeeperError
from ringkeeper.telegram.api import from_vault
from ringkeeper.vault import get_vault

logger = logging.getLogger(__name__)


def send(session: Session, settings: Settings, text: str, *, event: str | None = None) -> int:
    """Send `text` everywhere it's allowed; how many got it. With `event` (failures, say),
    only the channels set to send that."""
    sent = 0
    for channel in list_channels(session):
        if not channel.enabled or (event and event not in Prefs.load(channel.prefs).events):
            continue
        try:
            api = transport(settings, channel)
            try:
                api.post(Card(text))
            finally:
                api.close()
            sent += 1
        except (RingkeeperError, OSError):
            logger.warning("couldn't send to %s channel %s", channel.kind, channel.name)
    if settings.telegram and settings.telegram_chat_ids:
        try:
            bot = from_vault(get_vault(settings), settings)
            try:
                for chat in settings.telegram_chat_ids:
                    bot.send_message(chat, text)
                    sent += 1
            finally:
                bot.close()
        except (RingkeeperError, OSError):
            logger.warning("couldn't send to Telegram")
    return sent
