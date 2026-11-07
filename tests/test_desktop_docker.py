"""Tasks in Docker on the desktop: where each task runs, and what happens when Docker isn't ready."""

import json
import threading
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import httpx2
import pytest
from docker.errors import APIError, DockerException, ImageNotFound

from ringkeeper.config import Settings
from ringkeeper.workers import desktop_docker
from ringkeeper.workers.desktop_docker import DesktopRuntime, for_docker, image_for, image_size
from ringkeeper.workers.env import TASK_ENV, TOKEN_ENV
from ringkeeper.workers.runtime import LABEL_SESSION, WorkerSpec

GATEWAY = "http://127.0.0.1:54321"
SESSION = "2f1d9e3a-6c1b-4a55-9f7a-3b1f0a1c9e11"
IMAGE = "ghcr.io/alexyushkin/ringkeeper-worker:0.1.0-beta.28"


def spec() -> WorkerSpec:
    task = {"mcp_servers": {"ringkeeper-memory": f"{GATEWAY}/mcp/memory"}}
    return WorkerSpec(
        name="ringkeeper-worker-1",
        image="ringkeeper-worker:latest",
        env={
            TASK_ENV: json.dumps(task),
            TOKEN_ENV: "a-token",
            "ANTHROPIC_BASE_URL": f"{GATEWAY}/model/anthropic",
        },
        workspace="C:\\clients\\acme",
        state_volume="ringkeeper-state-1",
        network="ringkeeper_workers",
        labels={LABEL_SESSION: SESSION},
    )


class FakeRuntime:
    def __init__(self, name: str) -> None:
        self.name = name
        self.started: list[WorkerSpec] = []
        self.killed: list[str] = []
        self.removed: list[str] = []
        self.volumes = [f"{name}-volume"]

    def start(self, spec: WorkerSpec) -> str:
        self.started.append(spec)
        return f"{self.name}-1"

    def lines(self, container_id: str) -> Iterator[str]:
        yield f"{self.name}:{container_id}"

    def wait(self, container_id: str) -> int:
        return 7 if self.name == "docker" else 0

    def kill(self, container_id: str) -> None:
        self.killed.append(container_id)

    def remove(self, container_id: str) -> None:
        self.removed.append(container_id)

    def running_workers(self) -> dict[str, str]:
        return {f"{self.name}-9": "a-session"}

    def state_volumes(self) -> list[str]:
        return list(self.volumes)

    def remove_volume(self, name: str) -> bool:
        return name in self.volumes


class FakeImages:
    def __init__(self, present: bool) -> None:
        self.present = present

    def get(self, _image: str) -> object:
        if not self.present:
            raise ImageNotFound("no such image")
        return object()


class FakeClient:
    def __init__(self, *, present: bool = True) -> None:
        self.images = FakeImages(present)


class World:
    def __init__(
        self,
        *,
        on: bool = True,
        client: FakeClient | None = None,
        version: str | None = "0.1.0-beta.28",
    ) -> None:
        self.process, self.docker = FakeRuntime("process"), FakeRuntime("docker")
        self.notes: list[tuple[str, str]] = []
        settings = Settings(
            docker_tasks=on, app_version=version, gateway_internal_url=GATEWAY, mode="desktop"
        )
        self.runtime = DesktopRuntime(
            self.process,
            lambda: settings,
            connector=lambda: client,
            make_docker=lambda _client: self.docker,
            note=lambda session, text: self.notes.append((session, text)),
        )


def test_off_by_default() -> None:
    assert Settings().docker_tasks is False


def test_off_a_task_runs_as_a_process_and_nothing_is_said() -> None:
    world = World(on=False, client=FakeClient())
    assert world.runtime.start(spec()) == "process-1"
    assert world.process.started == [spec()]
    assert world.notes == []


def test_on_a_task_runs_in_docker_and_reaches_the_gateway_by_docker_s_name() -> None:
    world = World(client=FakeClient())
    assert world.runtime.start(spec()) == "docker-1"
    [started] = world.docker.started
    assert started.image == IMAGE
    assert started.network == "bridge"
    assert started.extra_hosts["host.docker.internal"] == "host-gateway"
    assert started.env["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:54321/model/anthropic"
    assert "host.docker.internal:54321/mcp/memory" in started.env[TASK_ENV]
    assert "127.0.0.1" not in json.dumps(started.env)
    assert started.env[TOKEN_ENV] == "a-token"
    assert started.workspace == spec().workspace
    assert world.process.started == []
    assert world.notes == []


@pytest.mark.parametrize(
    ("client", "says"),
    [(None, "Docker wasn't running"), (FakeClient(present=False), "isn't downloaded")],
    ids=["not-running", "no-image"],
)
def test_when_docker_isn_t_ready_the_task_runs_and_says_so(
    client: FakeClient | None, says: str
) -> None:
    world = World(client=client)
    assert world.runtime.start(spec()) == "process-1"
    assert world.process.started == [spec()]  # as it would have been: nothing rewritten
    [(session, text)] = world.notes
    assert session == SESSION
    assert says in text
    assert "without Docker's isolation" in text


def test_a_build_with_no_image_of_its_own_stays_local() -> None:
    world = World(client=FakeClient(), version=None)
    assert world.runtime.start(spec()) == "process-1"
    assert world.notes == []


def test_each_call_goes_to_the_runtime_that_started_the_task() -> None:
    world = World(client=FakeClient())
    ident = world.runtime.start(spec())
    assert list(world.runtime.lines(ident)) == ["docker:docker-1"]
    assert world.runtime.wait(ident) == 7
    world.runtime.kill(ident)
    world.runtime.remove(ident)
    assert (world.docker.killed, world.docker.removed) == (["docker-1"], ["docker-1"])
    assert world.process.killed == []


def test_both_runtimes_are_seen_for_cleaning_up() -> None:
    world = World(client=FakeClient())
    assert set(world.runtime.running_workers()) == {"process-9", "docker-9"}
    assert set(world.runtime.state_volumes()) == {"process-volume", "docker-volume"}
    assert world.runtime.remove_volume("docker-volume")
    assert world.runtime.remove_volume("process-volume")
    assert not world.runtime.remove_volume("neither")


def test_a_worker_that_outlived_a_restart_is_still_found() -> None:
    world = World(client=FakeClient())
    world.runtime.running_workers()
    world.runtime.kill("docker-9")
    assert world.docker.killed == ["docker-9"]


def test_docker_stopping_doesn_t_break_cleaning_up() -> None:
    world = World(client=None)
    assert set(world.runtime.running_workers()) == {"process-9"}
    assert world.runtime.state_volumes() == ["process-volume"]


def test_the_rewrite_keeps_everything_else() -> None:
    before = spec()
    after = for_docker(before, IMAGE, GATEWAY)
    assert replace(after, image=before.image, network=before.network, env=before.env,
                   extra_hosts=before.extra_hosts) == before  # fmt: skip


def test_the_image_follows_the_app_s_version() -> None:
    assert image_for(Settings(app_version="0.1.0-beta.28")) == IMAGE
    assert image_for(Settings()) is None


def registry(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path
    if path == "/token":
        return httpx2.Response(200, json={"token": "anonymous"})
    if path.endswith("/manifests/sha256:amd"):
        return httpx2.Response(200, json={"layers": [{"size": 300}, {"size": 200}]})
    if path.endswith("/manifests/sha256:arm"):
        return httpx2.Response(200, json={"layers": [{"size": 9}]})
    if path.endswith("/manifests/0.1.0-beta.28"):
        return httpx2.Response(
            200,
            json={
                "manifests": [
                    {"digest": "sha256:arm", "platform": {"architecture": "arm64"}},
                    {"digest": "sha256:amd", "platform": {"architecture": "amd64"}},
                ]
            },
        )
    return httpx2.Response(404, json={})


def test_the_size_is_read_from_the_registry_for_this_computer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop_docker, "_architecture", lambda: "amd64")
    assert image_size(IMAGE, httpx2.MockTransport(registry)) == 500
    monkeypatch.setattr(desktop_docker, "_architecture", lambda: "arm64")
    assert image_size(IMAGE, httpx2.MockTransport(registry)) == 9


def test_an_image_not_published_has_no_size() -> None:
    missing = IMAGE.replace("0.1.0-beta.28", "9.9.9")
    assert image_size(missing, httpx2.MockTransport(registry)) is None


def test_docker_not_running_is_an_answer_not_an_error() -> None:
    def refuses() -> Any:
        raise DockerException("no pipe")

    assert desktop_docker.connect(refuses) is None


class Containers:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.seen: dict[str, Any] = {}

    def run(self, image: str, **options: Any) -> bytes:
        self.seen = {"image": image, **options}
        if self.error:
            raise self.error
        return b""


class CheckClient:
    def __init__(self, containers: Containers) -> None:
        self.containers = containers


def test_turning_it_on_proves_a_container_can_reach_the_gateway() -> None:
    containers = Containers()
    assert desktop_docker.check(CheckClient(containers), IMAGE, GATEWAY) is None
    assert containers.seen["image"] == IMAGE
    assert "host.docker.internal:54321/health" in containers.seen["entrypoint"][2]
    assert containers.seen["cap_drop"] == ["ALL"]
    assert containers.seen["read_only"] is True


def test_a_gateway_a_container_can_t_reach_is_said_plainly() -> None:
    containers = Containers(APIError("exit 1"))
    said = desktop_docker.check(CheckClient(containers), IMAGE, GATEWAY)
    assert said is not None
    assert "couldn't reach Ringkeeper" in said


class Pulls:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.asked: tuple[str, str] | None = None

    def pull(self, name: str, *, tag: str, stream: bool, decode: bool) -> Iterator[dict[str, Any]]:
        assert stream
        assert decode
        self.asked = (name, tag)
        yield from self.events


class PullClient:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.api = Pulls(events)


def wait_for(download: desktop_docker.ImageDownload) -> dict[str, Any]:
    for _ in range(200):
        status = download.status()
        if not status["downloading"]:
            return status
        threading.Event().wait(0.01)
    raise AssertionError("the download didn't end")


def test_a_download_reports_its_progress() -> None:
    client = PullClient(
        [
            {"status": "Downloading", "id": "a", "progressDetail": {"current": 5, "total": 10}},
            {"status": "Downloading", "id": "b", "progressDetail": {"current": 1, "total": 30}},
            {"status": "Downloading", "id": "a", "progressDetail": {"current": 10, "total": 10}},
            {"status": "Pull complete", "id": "a"},
        ]
    )
    download = desktop_docker.ImageDownload()
    download.start(lambda: client, IMAGE)
    status = wait_for(download)
    assert (status["downloaded"], status["total"], status["error"]) == (11, 40, None)
    assert client.api.asked == (IMAGE.rsplit(":", 1)[0], "0.1.0-beta.28")


def test_a_download_with_docker_gone_says_so_in_plain_words() -> None:
    download = desktop_docker.ImageDownload()
    download.start(lambda: None, IMAGE)
    assert "didn't finish" in str(wait_for(download)["error"])


@pytest.mark.parametrize(
    ("settings", "client", "says"),
    [
        (Settings(), FakeClient(), "doesn't have a Docker image"),
        (Settings(app_version="1"), None, "Docker isn't running"),
        (Settings(app_version="1"), FakeClient(present=False), "Download the image first"),
    ],
    ids=["no-image-for-this-build", "not-running", "not-downloaded"],
)
def test_it_can_t_be_turned_on_until_it_can_work(
    settings: Settings, client: FakeClient | None, says: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop_docker, "connect", lambda: client)
    said = desktop_docker.turn_on(settings)
    assert said is not None
    assert says in said
