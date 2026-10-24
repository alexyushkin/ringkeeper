"""Voice notes to text against a mocked Bot API and stt service: limits and errors."""

import httpx2
import pytest

from ringkeeper import voice
from ringkeeper.config import Settings
from ringkeeper.telegram import api as telegram_api
from ringkeeper.telegram.api import BotApi
from ringkeeper.voice import SttClient, VoiceError, VoiceNote
from tests.fake_telegram import AUDIO_MARKER, BOT_TOKEN, FakeStt, FakeTelegram


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeTelegram, FakeStt]:
    telegram, stt = FakeTelegram(), FakeStt()
    monkeypatch.setattr(telegram_api, "transport", httpx2.MockTransport(telegram.handle))
    monkeypatch.setattr(voice, "transport", httpx2.MockTransport(stt.handle))
    return telegram, stt


def settings(**overrides: object) -> Settings:
    return Settings.model_validate({"stt_url": "http://stt:9000", **overrides})


def note(telegram: FakeTelegram, **kwargs: int) -> VoiceNote:
    telegram.voice("v1", **kwargs)
    found = VoiceNote.from_message(telegram.updates[-1]["message"])
    assert found is not None
    return found


def run(telegram: FakeTelegram, n: VoiceNote, config: Settings | None = None) -> str:
    config = config or settings()
    return voice.transcribe(BotApi(BOT_TOKEN), SttClient(config), n, config)


def test_a_voice_note_is_downloaded_and_transcribed(fakes: tuple[FakeTelegram, FakeStt]) -> None:
    telegram, stt = fakes
    text = run(telegram, note(telegram))
    assert text == "add a line to acme-web's notes saying voice works"
    assert telegram.downloads == ["v1"]
    [audio] = stt.received
    assert audio.startswith(AUDIO_MARKER)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"seconds": 252}, "That voice note is 4:12; the limit is 3:00. Send a shorter one."),
        ({"size": 6_200_000}, "That voice note is too big (6.2 MB); the limit is 5.0 MB."),
    ],
)
def test_limits_from_telegrams_metadata_stop_it_before_downloading(
    fakes: tuple[FakeTelegram, FakeStt], kwargs: dict[str, int], message: str
) -> None:
    telegram, stt = fakes
    with pytest.raises(VoiceError) as raised:
        run(telegram, note(telegram, **kwargs))
    assert str(raised.value).startswith(message)
    assert telegram.downloads == []
    assert stt.received == []


def test_the_download_stops_at_the_cap_when_telegram_under_reports(
    fakes: tuple[FakeTelegram, FakeStt],
) -> None:
    telegram, stt = fakes
    telegram.hide_file_size = True
    n = note(telegram, size=6_000, reported_size=100)
    with pytest.raises(VoiceError, match="too big; the limit is 4 KB"):
        run(telegram, n, settings(stt_max_bytes=4_000))
    assert stt.received == []


def test_the_stt_services_own_duration_check(fakes: tuple[FakeTelegram, FakeStt]) -> None:
    telegram, stt = fakes
    stt.status, stt.error, stt.seconds = 413, "too_long", 200.0
    with pytest.raises(VoiceError, match="That voice note is 3:20; the limit is 3:00"):
        run(telegram, note(telegram, seconds=10))  # Telegram said 10 s; the audio says 200


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ({"status": 422, "error": "undecodable"}, voice.UNREADABLE),
        ({"status": 500, "error": "failed"}, voice.UNREACHABLE),
        ({"down": True}, voice.UNREACHABLE),
        ({"text": "  "}, voice.SILENT),
    ],
)
def test_stt_failures_become_plain_messages(
    fakes: tuple[FakeTelegram, FakeStt], setup: dict[str, object], message: str
) -> None:
    telegram, stt = fakes
    for key, value in setup.items():
        setattr(stt, key, value)
    with pytest.raises(VoiceError) as raised:
        run(telegram, note(telegram))
    assert str(raised.value) == message


def test_voice_is_off_without_an_stt_url() -> None:
    with pytest.raises(VoiceError, match="Voice isn't set up"):
        SttClient(settings(stt_url=""))


def test_download_errors_never_show_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {request.url}")

    monkeypatch.setattr(telegram_api, "transport", httpx2.MockTransport(broken))
    with pytest.raises(telegram_api.TelegramError) as raised:
        BotApi(BOT_TOKEN).download("voice/v1.oga", 1000)
    assert BOT_TOKEN not in str(raised.value)
