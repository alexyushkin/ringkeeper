"""Voice notes to text: check the limits, download, transcribe, drop the audio.

The audio is only ever a bytes object in memory for one call: it is never
written to disk or the database, and it is dropped whether transcription
works or not. Limits are checked three times: from Telegram's metadata
before downloading, while downloading (the download stops at the cap), and
by the stt service on the decoded audio.
"""

from dataclasses import dataclass
from typing import Any, cast

import httpx2
from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.errors import RingkeeperError
from ringkeeper.models import Instance, Project
from ringkeeper.telegram.api import BotApi, FileTooLargeError, TelegramError

# Tests swap in httpx2.MockTransport.
transport: httpx2.BaseTransport | None = None

NOT_SET_UP = "Voice isn't set up on this install. Type it instead."
UNREACHABLE = "I couldn't transcribe that right now. Try again in a minute, or type it."
UNREADABLE = "I couldn't read that audio. Try recording it again."
SILENT = "I couldn't hear anything in that. Try again."
# Words the speech model wouldn't know how to spell, besides project and connection names.
COMMON_WORDS = ("Ringkeeper", "n8n", "Supabase", "README")
HINT_CHARS = 500


class VoiceError(RingkeeperError):
    """Something to tell the user, in a sentence they can act on."""


@dataclass(frozen=True)
class VoiceNote:
    file_id: str
    seconds: int
    size: int | None

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "VoiceNote | None":
        voice = cast(dict[str, Any] | None, message.get("voice"))
        if not voice:
            return None
        size = voice.get("file_size")
        return cls(str(voice["file_id"]), int(voice.get("duration") or 0), size and int(size))


def clock(seconds: float) -> str:
    whole = round(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def megabytes(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB" if size >= 100_000 else f"{size // 1000} KB"


def too_long(seconds: float, limit: float) -> VoiceError:
    return VoiceError(
        f"That voice note is {clock(seconds)}; the limit is {clock(limit)}. Send a shorter one."
    )


def too_big(size: int | None, limit: int) -> VoiceError:
    what = f" ({megabytes(size)})" if size else ""
    return VoiceError(
        f"That voice note is too big{what}; the limit is {megabytes(limit)}. Send a shorter one."
    )


class SttClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.stt_url:
            raise VoiceError(NOT_SET_UP)
        self.settings = settings
        self._http = httpx2.Client(
            base_url=settings.stt_url, timeout=settings.stt_timeout_seconds, transport=transport
        )

    def close(self) -> None:
        self._http.close()

    def transcribe(self, audio: bytes, hint: str | None = None) -> str:
        try:
            response = self._http.post(
                "/transcribe",
                content=audio,
                params={"hint": hint} if hint else None,
                headers={"Content-Type": "application/octet-stream"},
            )
            body = cast(dict[str, Any], response.json())
        except (httpx2.HTTPError, ValueError):
            raise VoiceError(UNREACHABLE) from None
        match response.status_code, body.get("error"):
            case 200, _:
                text = str(body.get("text") or "").strip()
                if not text:
                    raise VoiceError(SILENT)
                return text
            case 413, "too_long":
                raise too_long(float(body.get("seconds") or 0), self.settings.stt_max_seconds)
            case 413, _:
                raise too_big(len(audio), self.settings.stt_max_bytes)
            case 422, _:
                raise VoiceError(UNREADABLE)
            case _:
                raise VoiceError(UNREACHABLE)


def vocabulary(session: Session) -> str:
    """Names the speaker is likely to say, as a hint for the speech model."""
    names: list[str] = []
    for project in session.scalars(select(Project).order_by(Project.name)):
        names += [project.name, *project.aliases]
    names += session.scalars(select(Instance.name).order_by(Instance.name))
    names += COMMON_WORDS
    hint = ", ".join(dict.fromkeys(n for n in names if n))
    return hint[:HINT_CHARS].rsplit(", ", 1)[0] if len(hint) > HINT_CHARS else hint


def check_limits(note: VoiceNote, settings: Settings) -> None:
    """From Telegram's metadata alone, before a byte is downloaded."""
    if note.seconds > settings.stt_max_seconds:
        raise too_long(note.seconds, settings.stt_max_seconds)
    if note.size is not None and note.size > settings.stt_max_bytes:
        raise too_big(note.size, settings.stt_max_bytes)


def transcribe(
    api: BotApi, stt: SttClient, note: VoiceNote, settings: Settings, hint: str | None = None
) -> str:
    check_limits(note, settings)
    try:
        info = api.get_file(note.file_id)
        size = info.get("file_size")
        if size is not None and int(size) > settings.stt_max_bytes:
            raise too_big(int(size), settings.stt_max_bytes)
        audio = api.download(str(info["file_path"]), settings.stt_max_bytes)
    except FileTooLargeError:
        raise too_big(None, settings.stt_max_bytes) from None
    except (TelegramError, KeyError):
        raise VoiceError("I couldn't download that voice note from Telegram. Try again.") from None
    try:
        return stt.transcribe(audio, hint)
    finally:
        del audio
