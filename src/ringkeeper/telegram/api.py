"""The few Bot API methods Ringkeeper uses, over long polling (no webhook)."""

import re
from typing import TYPE_CHECKING, Any, cast

import httpx2

from ringkeeper import scrub
from ringkeeper.errors import RingkeeperError

if TYPE_CHECKING:
    from ringkeeper.config import Settings
    from ringkeeper.vault import Vault

# Tests swap in httpx2.MockTransport.
transport: httpx2.BaseTransport | None = None


class TelegramError(RingkeeperError):
    pass


_URL = re.compile(r"(?i)\b(https?)://([^\s/?#<>\"'`]+)")


def defang(text: str) -> str:
    """http://evil.example/x -> hxxp://evil[.]example/x: links in what an agent or a
    client's data says aren't clickable (and Telegram shows no preview anyway)."""

    def neutral(match: re.Match[str]) -> str:
        scheme = "hxxps" if match.group(1).lower() == "https" else "hxxp"
        return f"{scheme}://{match.group(2).replace('.', '[.]')}"

    return _URL.sub(neutral, text)


NO_PREVIEW = {"is_disabled": True}


class FileTooLargeError(TelegramError):
    pass


class BotApi:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org") -> None:
        scrub.register(token)  # before any call: an error or log line never shows it
        self._http = httpx2.Client(
            base_url=f"{base_url.rstrip('/')}/bot{token}/", timeout=60.0, transport=transport
        )
        self._files = f"{base_url.rstrip('/')}/file/bot{token}/"

    def close(self) -> None:
        self._http.close()

    def call(self, method: str, **params: Any) -> Any:
        body = {k: v for k, v in params.items() if v is not None}
        try:
            response = self._http.post(method, json=body)
        except httpx2.HTTPError as error:
            # Never include the URL: it carries the bot token.
            raise TelegramError(f"Telegram {method} failed: {type(error).__name__}") from None
        try:
            data = cast(dict[str, Any], response.json())
        except ValueError:
            raise TelegramError(f"Telegram {method}: HTTP {response.status_code}") from None
        if not data.get("ok"):
            raise TelegramError(f"Telegram {method}: {data.get('description', 'not ok')}")
        return data.get("result")

    def get_updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        allowed = ["message", "callback_query"]
        result = self.call("getUpdates", offset=offset, timeout=timeout, allowed_updates=allowed)
        return cast(list[dict[str, Any]], result or [])

    def send_message(
        self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> int:
        result = self.call(
            "sendMessage", chat_id=chat_id, text=defang(text), reply_markup=reply_markup,
            link_preview_options=NO_PREVIEW,
        )  # fmt: skip
        return int(cast(dict[str, Any], result)["message_id"])

    def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        self.call(
            "editMessageText", chat_id=chat_id, message_id=message_id, text=defang(text),
            reply_markup={"inline_keyboard": []}, link_preview_options=NO_PREVIEW,
        )  # fmt: skip

    def answer_callback(self, callback_id: str, text: str) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:180])

    def get_me(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.call("getMe"))

    def get_file(self, file_id: str) -> dict[str, Any]:
        """{file_id, file_size, file_path}: where to download a file from."""
        return cast(dict[str, Any], self.call("getFile", file_id=file_id))

    def download(self, file_path: str, max_bytes: int) -> bytes:
        """The file's bytes, in memory only; stops as soon as it passes max_bytes."""
        chunks: list[bytes] = []
        size = 0
        try:
            with self._http.stream("GET", self._files + file_path) as response:
                if response.status_code != 200:
                    raise TelegramError(f"Telegram file download: HTTP {response.status_code}")
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        chunks.clear()
                        raise FileTooLargeError(f"the file is larger than {max_bytes} bytes")
                    chunks.append(chunk)
        except httpx2.HTTPError as error:
            # Never include the URL: it carries the bot token.
            raise TelegramError(f"Telegram file download failed: {type(error).__name__}") from None
        return b"".join(chunks)


def recent_chats(updates: list[dict[str, Any]], code: str | None = None) -> list[dict[str, Any]]:
    """The chats in a batch of updates (for picking the ones to allow), newest last.

    With `code`, only chats that sent it: anyone can message a bot, so the web
    app shows a pairing code and lists just the chat that typed it.
    """
    seen: dict[int, dict[str, Any]] = {}
    for update in updates:
        message = cast(dict[str, Any], update.get("message") or {})
        chat = cast(dict[str, Any], message.get("chat") or {})
        if code is not None and code.upper() not in str(message.get("text") or "").upper():
            continue
        if "id" in chat:
            names = (chat.get("first_name"), chat.get("last_name"))
            seen[int(chat["id"])] = {
                "id": int(chat["id"]),
                "type": str(chat.get("type", "-")),
                "name": chat.get("title") or " ".join(str(n) for n in names if n) or "-",
                "username": chat.get("username"),
            }
    return list(seen.values())


def from_vault(vault: "Vault", settings: "Settings") -> BotApi:
    try:
        token = vault.resolve(settings.telegram_bot_token_ref).reveal()
    except RingkeeperError as error:
        raise TelegramError(
            f"no Telegram bot token ({error}); run `ringkeeper telegram login`"
        ) from None
    return BotApi(token, settings.telegram_api_url)
