from typing import Annotated

import typer

from ringkeeper.claude_auth import normalize
from ringkeeper.cli.auth import read_credential
from ringkeeper.cli.common import echo_table, fail, handle_errors
from ringkeeper.config import get_settings
from ringkeeper.telegram.api import BotApi, TelegramError, from_vault, recent_chats
from ringkeeper.vault import get_vault

app = typer.Typer(
    help="Telegram notifications for the inbox (a notification channel, not a secure one).",
    no_args_is_help=True,
)


@app.command("login")
def login() -> None:
    """Store the bot token from @BotFather in the vault (piped stdin or a hidden prompt)."""
    settings = get_settings()
    with handle_errors():
        token = normalize(read_credential("Telegram bot token"))
        if ":" not in token:
            raise TelegramError("that doesn't look like a bot token (expected <id>:<secret>)")
        api = BotApi(token, settings.telegram_api_url)
        try:
            me = api.get_me()
        finally:
            api.close()
        get_vault(settings).store(settings.telegram_bot_token_ref, token)
    typer.echo(f"stored the token for @{me.get('username')} at {settings.telegram_bot_token_ref}")
    typer.echo("Next: message the bot from your phone, then run `ringkeeper telegram chats`.")


@app.command("chats")
def chats() -> None:
    """List chats that recently messaged the bot, to find the IDs to allow."""
    settings = get_settings()
    with handle_errors():
        api = from_vault(get_vault(settings), settings)
        try:
            updates = api.get_updates(None, 0)
        finally:
            api.close()
    seen = [
        [
            str(chat["id"]), chat["type"], chat["username"] or chat["name"],
            "yes" if chat["id"] in settings.telegram_chat_ids else "no",
        ]
        for chat in recent_chats(updates)
    ]  # fmt: skip
    echo_table(
        ["CHAT ID", "TYPE", "NAME", "ALLOWED"],
        seen,
        empty="no recent messages; send the bot a message and try again",
    )
    if seen:
        typer.echo("Allow a chat with RINGKEEPER_TELEGRAM_CHAT_IDS='[<id>]' in .env.")


@app.command("test")
def send_test(
    text: Annotated[str, typer.Argument()] = "Ringkeeper is connected.",
) -> None:
    """Send a test message to every allowed chat."""
    settings = get_settings()
    if not settings.telegram_chat_ids:
        raise fail("no chats are allowed; set RINGKEEPER_TELEGRAM_CHAT_IDS first")
    with handle_errors():
        api = from_vault(get_vault(settings), settings)
        try:
            for chat in settings.telegram_chat_ids:
                api.send_message(chat, text)
                typer.echo(f"sent to {chat}")
        finally:
            api.close()
