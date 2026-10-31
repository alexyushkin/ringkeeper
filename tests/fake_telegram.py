"""A fake Bot API and a fake stt service, as httpx2 mock transports."""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx2

BOT_TOKEN = "123456:fake-telegram-bot-token-abcdef"
ME = 1001  # the allowed chat
STRANGER = 666
# What a fake voice note's bytes look like; tests search for it where audio must not be.
AUDIO_MARKER = b"OggS-fake-voice-note-"


@dataclass
class FakeTelegram:
    updates: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    sent: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    edits: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    callbacks: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    offsets: list[int | None] = field(default_factory=list[int | None])
    paths: list[str] = field(default_factory=list[str])
    files: dict[str, bytes] = field(default_factory=dict[str, bytes])  # file_id -> bytes
    downloads: list[str] = field(default_factory=list[str])  # file ids downloaded
    hide_file_size: bool = False  # getFile without file_size, as Telegram may answer
    next_id: int = 500

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.paths.append(request.url.path)
        files = f"/file/bot{BOT_TOKEN}/"
        if request.url.path.startswith(files):
            file_id = request.url.path.removeprefix(files).removeprefix("voice/")
            file_id = file_id.removesuffix(".oga")
            if file_id not in self.files:
                return httpx2.Response(404)
            self.downloads.append(file_id)
            return httpx2.Response(200, content=self.files[file_id])
        prefix = f"/bot{BOT_TOKEN}/"
        if not request.url.path.startswith(prefix):
            return httpx2.Response(401, json={"ok": False, "description": "Unauthorized"})
        method = request.url.path.removeprefix(prefix)
        body: dict[str, Any] = json.loads(request.content or b"{}")
        match method:
            case "getMe":
                return self.ok({"id": 9, "is_bot": True, "username": "ringkeeper_test_bot"})
            case "getUpdates":
                self.offsets.append(body.get("offset"))
                offset = body.get("offset") or 0
                return self.ok([u for u in self.updates if u["update_id"] >= offset])
            case "sendMessage":
                self.next_id += 1
                self.sent.append({**body, "message_id": self.next_id})
                return self.ok({"message_id": self.next_id})
            case "editMessageText":
                self.edits.append(body)
                return self.ok(True)
            case "answerCallbackQuery":
                self.callbacks.append(body)
                return self.ok(True)
            case "getFile":
                file_id = str(body["file_id"])
                if file_id not in self.files:
                    return httpx2.Response(400, json={"ok": False, "description": "bad file_id"})
                info: dict[str, Any] = {"file_id": file_id, "file_path": f"voice/{file_id}.oga"}
                if not self.hide_file_size:
                    info["file_size"] = len(self.files[file_id])
                return self.ok(info)
            case _:
                return httpx2.Response(404, json={"ok": False, "description": "Not Found"})

    @staticmethod
    def ok(result: object) -> httpx2.Response:
        return httpx2.Response(200, json={"ok": True, "result": result})

    def _message(self, chat: int, user: int = 42, **fields: Any) -> None:
        message = {
            "message_id": 900 + len(self.updates),
            "from": {"id": user, "username": "alice"},
            "chat": {"id": chat, "type": "private", "first_name": "Alice"},
            **fields,
        }
        self.updates.append({"update_id": len(self.updates) + 1, "message": message})

    def press(self, data: str, chat: int = ME, user: int = 42) -> None:
        self.updates.append(
            {
                "update_id": len(self.updates) + 1,
                "callback_query": {
                    "id": f"cb{len(self.updates)}",
                    "from": {"id": user, "username": "alice"},
                    "message": {"message_id": 1, "chat": {"id": chat}},
                    "data": data,
                },
            }
        )

    def reply(self, to_message: int, text: str, chat: int = ME) -> None:
        self._message(chat, text=text, reply_to_message={"message_id": to_message})

    def text(self, text: str, chat: int = ME, user: int = 42, **fields: Any) -> None:
        self._message(chat, user, text=text, **fields)

    def voice(
        self,
        file_id: str,
        *,
        seconds: int = 3,
        size: int = 2000,
        reply_to: int | None = None,
        chat: int = ME,
        reported_size: int | None = None,
        user: int = 42,
    ) -> None:
        """A voice note of `size` bytes; Telegram reports `reported_size` (default: the truth)."""
        self.files[file_id] = (AUDIO_MARKER + file_id.encode()).ljust(size, b"\x01")
        voice = {
            "file_id": file_id, "file_unique_id": f"u-{file_id}", "duration": seconds,
            "mime_type": "audio/ogg", "file_size": size if reported_size is None else reported_size,
        }  # fmt: skip
        extra: dict[str, Any] = {"voice": voice}
        if reply_to is not None:
            extra["reply_to_message"] = {"message_id": reply_to}
        self._message(chat, user, **extra)


@dataclass
class FakeStt:
    """The stt service: answers with `text`, or with an error status."""

    text: str = "add a line to acme-web's notes saying voice works"
    status: int = 200
    error: str | None = None
    seconds: float = 3.0
    received: list[bytes] = field(default_factory=list[bytes])
    hints: list[str | None] = field(default_factory=list[str | None])
    languages: list[str | None] = field(default_factory=list[str | None])
    down: bool = False

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.down:
            raise httpx2.ConnectError("stt is down")
        assert request.url.path == "/transcribe"
        self.received.append(request.content)
        self.hints.append(request.url.params.get("hint"))
        self.languages.append(request.url.params.get("language"))
        if self.status != 200:
            body: dict[str, Any] = {"error": self.error}
            if self.error == "too_long":
                body |= {"seconds": self.seconds, "max_seconds": 180}
            return httpx2.Response(self.status, json=body)
        return httpx2.Response(
            200, json={"text": self.text, "language": "en", "duration": self.seconds}
        )
