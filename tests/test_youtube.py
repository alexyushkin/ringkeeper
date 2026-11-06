"""YouTube through a Google account, against a fake: privacy is always sent and private unless
the call says otherwise, what waits and what needs the name typed, and a time that has passed."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest

from ringkeeper import destructive
from ringkeeper.adapters import google
from ringkeeper.adapters.base import Target, ToolError, ToolKind, ToolSpec
from ringkeeper.db import session_scope
from ringkeeper.models import Env, InstanceType
from ringkeeper.services import google_oauth
from ringkeeper.vault import Secret
from tests.helpers import make_project

pytestmark = pytest.mark.anyio
ALL_ACCESS = ["youtube_read", "youtube_upload", "youtube_manage", "youtube_analytics"]
LATER = (datetime.now(UTC) + timedelta(days=2)).isoformat()
PAST = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()


class Api:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.received = b""

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.method == "POST" and "/upload/" in request.url.path:
            return httpx2.Response(200, headers={"Location": "https://upload.example/session-1"})
        if request.url.host == "upload.example":
            self.received += request.content
            total = int(request.headers["content-range"].rsplit("/", 1)[1])
            if len(self.received) < total:
                return httpx2.Response(308)
            return httpx2.Response(200, json={"id": "vid-1", "status": {"privacyStatus": "x"}})
        if request.url.path.endswith("/videos") and request.method == "GET":
            return httpx2.Response(200, json={"items": [{
                "id": "abcdef1",
                "snippet": {"title": "Old", "categoryId": "22", "description": "D"},
                "status": {"privacyStatus": "private", "embeddable": True},
            }]})  # fmt: skip
        if request.url.path.endswith("/channels"):
            return httpx2.Response(200, json={"items": [{
                "id": "UC1", "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}},
            }]})  # fmt: skip
        if request.url.path.endswith("/playlistItems"):
            return httpx2.Response(200, json={"items": [
                {"contentDetails": {"videoId": "abcdef1"}},
            ]})  # fmt: skip
        return httpx2.Response(200, json={"id": "ok"})

    def sent(self, method: str) -> dict[str, Any]:
        found = [r for r in self.requests if r.method == method and r.content]
        return json.loads(found[-1].content)


@pytest.fixture
def api() -> Iterator[Api]:
    fake = Api()
    before = google.transport
    google.transport = httpx2.MockTransport(fake.handle)
    yield fake
    google.transport = before


@pytest.fixture
def folder(database_url: str, tmp_path: Path) -> Path:
    (tmp_path / "clip.mp4").write_bytes(b"v" * (google.UPLOAD_CHUNK + 100))
    (tmp_path / "notes.txt").write_text("not a video")
    with session_scope() as session:
        make_project(session, "acme-web", workspace_path=str(tmp_path))
    return tmp_path


def target(access: list[str] | None = None) -> Target:
    secret = json.dumps({"access_token": "at-1", "refresh_token": "rt-1"})
    return Target("t", Env.DEV, None, Secret("r", secret),
                  {"access": ALL_ACCESS if access is None else access}, "acme-web")  # fmt: skip


def spec(name: str) -> ToolSpec:
    return next(t for t in google.ADAPTER.tools if t.name == name)


def typed(tool: str, **args: Any) -> str | None:
    return destructive.reason(InstanceType.GOOGLE, tool, args)


def classify(name: str, args: dict[str, Any], connection: Target | None = None) -> ToolKind:
    found = spec(name).classify
    assert found is not None
    return found(connection or target(), args)


async def upload(**args: Any) -> dict[str, Any]:
    given = {"file": "clip.mp4", "title": "Demo", "made_for_kids": False, **args}
    return await spec("upload_video").handler(target(), **given)


# --- Privacy ---


async def test_privacy_is_always_sent_and_private_unless_told(api: Api, folder: Path) -> None:
    await upload()
    assert api.sent("POST")["status"]["privacyStatus"] == "private"


async def test_a_chosen_privacy_is_sent(api: Api, folder: Path) -> None:
    await upload(privacy="unlisted")
    assert api.sent("POST")["status"]["privacyStatus"] == "unlisted"


def test_a_private_upload_waits_and_anything_else_needs_the_name_typed() -> None:
    assert typed("upload_video") is None
    assert typed("upload_video", privacy="private") is None
    assert typed("upload_video", privacy="public") == google.PUBLIC_VIDEO
    assert typed("upload_video", privacy="unlisted") == google.PUBLIC_VIDEO
    assert typed("set_video_privacy", privacy="private") is None
    assert typed("set_video_privacy", privacy="public") == google.PUBLIC_VIDEO


@pytest.mark.parametrize(
    "tool", ["edit_video", "delete_video", "reply_to_comment", "delete_comment"]
)
def test_what_always_needs_the_name_typed(tool: str) -> None:
    assert typed(tool)


def test_every_youtube_write_always_asks() -> None:
    for tool in ("upload_video", "edit_video", "set_video_privacy", "delete_video",
                 "reply_to_comment", "delete_comment"):  # fmt: skip
        assert tool in google.ALWAYS_ASKS
        assert spec(tool).kind is ToolKind.WRITE


@pytest.mark.parametrize(
    "tool", ["youtube_channel", "youtube_videos", "youtube_comments", "youtube_report"]
)
def test_reading_runs(tool: str) -> None:
    assert spec(tool).kind is ToolKind.READ


# --- Scheduling ---


async def test_a_scheduled_video_stays_private_until_its_time(api: Api, folder: Path) -> None:
    await upload(publish_at=LATER)
    status = api.sent("POST")["status"]
    assert status["privacyStatus"] == "private"
    assert status["publishAt"] == LATER
    assert typed("upload_video", publish_at=LATER) is None


def test_a_time_thats_passed_is_refused_before_its_held() -> None:
    with pytest.raises(ToolError, match="has passed"):
        classify("upload_video", {"publish_at": PAST})


async def test_a_time_that_passes_while_the_card_waits_is_refused(api: Api, folder: Path) -> None:
    """YouTube would publish it at once, on an approval that never typed the name."""
    with pytest.raises(ToolError, match="has passed"):
        await upload(publish_at=PAST)
    assert not api.requests


async def test_the_same_goes_for_a_video_already_up(api: Api) -> None:
    with pytest.raises(ToolError, match="has passed"):
        await spec("set_video_privacy").handler(
            target(), video_id="abcdef1", privacy="private", publish_at=PAST
        )
    assert not api.requests


def test_a_schedule_needs_privacy_private_and_a_time_zone() -> None:
    with pytest.raises(ToolError, match="set privacy to private"):
        classify("upload_video", {"privacy": "public", "publish_at": LATER})
    with pytest.raises(ToolError, match="time zone"):
        classify("upload_video", {"publish_at": "2030-01-01T10:00:00"})
    with pytest.raises(ToolError, match="isn't a date"):
        classify("upload_video", {"publish_at": "tomorrow"})


# --- The file ---


async def test_a_video_goes_up_in_parts(api: Api, folder: Path) -> None:
    found = await upload()
    assert found["video_id"] == "vid-1"
    assert len(api.received) == google.UPLOAD_CHUNK + 100
    ranges = [r.headers["content-range"] for r in api.requests if r.method == "PUT"]
    assert ranges[0] == f"bytes 0-{google.UPLOAD_CHUNK - 1}/{google.UPLOAD_CHUNK + 100}"
    assert len(ranges) == 2


@pytest.mark.parametrize(
    ("file", "said"),
    [
        ("../secret.mp4", "isn't in the project's folder"),
        ("notes.txt", "won't take"),
        ("missing.mp4", "there's no missing.mp4"),
    ],
)
async def test_only_a_video_in_the_projects_folder(
    file: str, said: str, api: Api, folder: Path
) -> None:
    with pytest.raises(ToolError, match=said):
        await upload(file=file)
    assert not api.requests


def test_a_file_that_cant_go_is_refused_before_its_held(folder: Path) -> None:
    with pytest.raises(ToolError, match="isn't in the project's folder"):
        classify("upload_video", {"file": "../env.mp4"})
    assert classify("upload_video", {"file": "clip.mp4"}) is ToolKind.WRITE


async def test_a_stopped_upload_says_so(folder: Path) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            return httpx2.Response(200, headers={"Location": "https://upload.example/s"})
        return httpx2.Response(403, json={"error": {"message": "quota"}})

    google.transport = httpx2.MockTransport(refuse)
    try:
        with pytest.raises(ToolError, match="stopped the upload: quota"):
            await upload()
    finally:
        google.transport = None


# --- Editing, and what a connection was given ---


async def test_an_edit_keeps_what_it_doesnt_name(api: Api) -> None:
    await spec("edit_video").handler(target(), video_id="abcdef1", title="New")
    sent = api.sent("PUT")["snippet"]
    assert sent == {"title": "New", "description": "D", "categoryId": "22"}


async def test_changing_privacy_keeps_the_rest_of_the_status(api: Api) -> None:
    await spec("set_video_privacy").handler(target(), video_id="abcdef1", privacy="public")
    assert api.sent("PUT")["status"] == {"embeddable": True, "privacyStatus": "public"}


async def test_listing_videos_reads_the_uploads_list(api: Api) -> None:
    found = await spec("youtube_videos").handler(target())
    assert found["videos"][0]["id"] == "abcdef1"
    assert not any(r.url.path.endswith("/search") for r in api.requests)  # 100 units each


def test_a_connection_without_youtube_access_says_so() -> None:
    with pytest.raises(ToolError, match="YouTube"):
        classify("delete_video", {}, target(["youtube_read"]))


def test_youtube_is_chosen_at_sign_in() -> None:
    wanted = google_oauth.scopes_for(ALL_ACCESS)
    assert f"{google_oauth.G}youtube.force-ssl" in wanted
    assert f"{google_oauth.G}yt-analytics.readonly" in wanted
