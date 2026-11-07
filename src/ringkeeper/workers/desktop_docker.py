"""Tasks in Docker on the desktop: the server's worker image, through Docker Desktop.

Off by default. When it's on and Docker is ready, a task runs in a container that sees only its
project's folder, so it can't reach the vault, the keychain or the rest of the computer. Docker
not being ready doesn't stop the work: the task runs as a local process, and says so.

The gateway stays on this computer's loopback address. A container reaches it through Docker
Desktop's `host.docker.internal`, and `check` proves that works before the setting can be turned
on, rather than widening what the gateway listens on.
"""

import contextlib
import logging
import platform
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any, cast
from urllib.parse import urlsplit

import httpx2

from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.services import tasks
from ringkeeper.workers.runtime import (
    LABEL_SESSION,
    ContainerRuntime,
    DockerRuntime,
    WorkerSpec,
)

REGISTRY = "ghcr.io"
REPOSITORY = "alexyushkin/ringkeeper-worker"
HOST = "host.docker.internal"
NETWORK = "bridge"
PING_SECONDS = 3
REGISTRY_SECONDS = 15
NOTE = "ringkeeper.note"
MANIFESTS = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
NOT_RUNNING = (
    "Docker wasn't running, so this task ran on this computer without Docker's isolation. "
    "Start Docker Desktop to use it for the next task."
)
NOT_DOWNLOADED = (
    "Docker's image isn't downloaded, so this task ran on this computer without Docker's "
    "isolation. Download it in Settings > Security."
)

logger = logging.getLogger(__name__)
Client = Any  # docker.DockerClient, which only some processes import


def image_for(settings: Settings) -> str | None:
    """The image made for this version of the app. A source checkout has none."""
    return f"{REGISTRY}/{REPOSITORY}:{settings.app_version}" if settings.app_version else None


def _client() -> Client:
    import docker  # noqa: PLC0415 (only when asked for)

    return docker.from_env(timeout=PING_SECONDS)


def connect(make: Callable[[], Client] = _client) -> Client | None:
    """A client for a Docker that's running, or None."""
    from docker.errors import DockerException  # noqa: PLC0415

    try:
        client = make()
        client.ping()
    except (DockerException, OSError):
        return None
    return client


def has_image(client: Client, image: str) -> bool:
    from docker.errors import APIError, ImageNotFound  # noqa: PLC0415

    try:
        client.images.get(image)
    except (ImageNotFound, APIError):
        return False
    return True


def _architecture() -> str:
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"


def image_size(image: str, transport: httpx2.BaseTransport | None = None) -> int | None:
    """What downloading the image takes, in bytes, from the registry. None if it isn't there."""
    tag = image.rsplit(":", 1)[1]
    base = f"https://{REGISTRY}/v2/{REPOSITORY}"
    try:
        with httpx2.Client(timeout=REGISTRY_SECONDS, transport=transport) as http:
            token = http.get(
                f"https://{REGISTRY}/token",
                params={"scope": f"repository:{REPOSITORY}:pull", "service": REGISTRY},
            ).json()["token"]
            headers = {"Authorization": f"Bearer {token}", "Accept": MANIFESTS}
            found = http.get(f"{base}/manifests/{tag}", headers=headers)
            found.raise_for_status()
            manifest: dict[str, Any] = found.json()
            for entry in cast(list[dict[str, Any]], manifest.get("manifests") or []):
                if entry.get("platform", {}).get("architecture") == _architecture():
                    one = http.get(f"{base}/manifests/{entry['digest']}", headers=headers)
                    one.raise_for_status()
                    manifest = one.json()
                    break
    except (httpx2.HTTPError, KeyError, ValueError):
        return None
    layers = cast(list[dict[str, Any]], manifest.get("layers") or [])
    return sum(int(layer.get("size", 0)) for layer in layers) or None


def for_docker(spec: WorkerSpec, image: str, gateway: str) -> WorkerSpec:
    """A worker's spec as a container needs it: the gateway by the name Docker Desktop gives this
    computer. Its port and token stay as they are."""
    parts = urlsplit(gateway)
    inside = f"{parts.scheme}://{HOST}:{parts.port}"
    return replace(
        spec,
        image=image,
        network=NETWORK,
        env={name: value.replace(gateway.rstrip("/"), inside) for name, value in spec.env.items()},
        extra_hosts={**spec.extra_hosts, HOST: "host-gateway"},
    )


def check(client: Client, image: str, gateway: str) -> str | None:
    """Whether a container can reach the gateway: None if so, else what to tell the user."""
    from docker.errors import DockerException  # noqa: PLC0415

    port = urlsplit(gateway).port
    probe = f"import urllib.request as u;u.urlopen('http://{HOST}:{port}/health',timeout=5).read()"
    try:
        client.containers.run(
            image, entrypoint=["python", "-c", probe], remove=True, network=NETWORK,
            extra_hosts={HOST: "host-gateway"}, cap_drop=["ALL"], read_only=True,
            security_opt=["no-new-privileges"],
        )  # fmt: skip
    except DockerException:
        logger.exception("a container couldn't reach the gateway")
        return (
            "A test container couldn't reach Ringkeeper, so tasks can't run in Docker here. "
            "Tasks keep running on this computer."
        )
    return None


def _note(session_id: str, text: str) -> None:
    """Said in the task, before its first step."""
    import uuid  # noqa: PLC0415

    with session_scope() as session:
        tasks.add_event(session, uuid.UUID(session_id), 0, NOTE, {"message": text})


class DesktopRuntime:
    """A `ContainerRuntime` that picks where each task runs, and remembers."""

    def __init__(
        self,
        process: ContainerRuntime,
        settings: Callable[[], Settings],
        *,
        connector: Callable[[], Client | None] = connect,
        make_docker: Callable[[Client], ContainerRuntime] = DockerRuntime,
        note: Callable[[str, str], None] = _note,
    ) -> None:
        self.process = process
        self._settings = settings
        self._connect = connector
        self._make_docker = make_docker
        self._note = note
        self._owners: dict[str, ContainerRuntime] = {}
        self._lock = threading.Lock()

    def _docker(self) -> ContainerRuntime | None:
        client = self._connect()
        return self._make_docker(client) if client is not None else None

    def _route(self, spec: WorkerSpec) -> tuple[ContainerRuntime, WorkerSpec]:
        settings = self._settings()
        image = image_for(settings)
        if not settings.docker_tasks or image is None:
            return self.process, spec
        client = self._connect()
        problem = None
        if client is None:
            problem = NOT_RUNNING
        elif not has_image(client, image):
            problem = NOT_DOWNLOADED
        if problem is not None or client is None:
            self._note(spec.labels[LABEL_SESSION], problem or NOT_RUNNING)
            return self.process, spec
        return self._make_docker(client), for_docker(spec, image, settings.gateway_internal_url)

    def _owner(self, ident: str) -> ContainerRuntime:
        with self._lock:
            return self._owners.get(ident, self.process)

    # --- ContainerRuntime ---

    def start(self, spec: WorkerSpec) -> str:
        runtime, ready = self._route(spec)
        ident = runtime.start(ready)
        with self._lock:
            self._owners[ident] = runtime
        return ident

    def lines(self, container_id: str) -> Iterator[str]:
        return self._owner(container_id).lines(container_id)

    def wait(self, container_id: str) -> int:
        return self._owner(container_id).wait(container_id)

    def kill(self, container_id: str) -> None:
        self._owner(container_id).kill(container_id)

    def remove(self, container_id: str) -> None:
        self._owner(container_id).remove(container_id)
        with self._lock:
            self._owners.pop(container_id, None)

    def running_workers(self) -> dict[str, str]:
        found = dict(self.process.running_workers())
        docker = self._docker() if self._settings().docker_tasks else None
        if docker is not None:
            for ident, session in docker.running_workers().items():
                with self._lock:
                    self._owners.setdefault(ident, docker)
                found[ident] = session
        return found

    def state_volumes(self) -> list[str]:
        found = list(self.process.state_volumes())
        docker = self._docker() if self._settings().docker_tasks else None
        return found + (docker.state_volumes() if docker is not None else [])

    def remove_volume(self, name: str) -> bool:
        if self.process.remove_volume(name):
            return True
        docker = self._docker() if self._settings().docker_tasks else None
        return docker.remove_volume(name) if docker is not None else False


class ImageDownload:
    """Fetching the image once, in the background, for Settings to follow."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._done = 0
        self._total = 0
        self._error: str | None = None

    def start(self, make: Callable[[], Client | None], image: str) -> None:
        with self._lock:
            if self._running:
                return
            self._running, self._error, self._done, self._total = True, None, 0, 0
        threading.Thread(
            target=self._pull, args=(make, image), daemon=True, name="rk-docker-image"
        ).start()

    def _pull(self, make: Callable[[], Client | None], image: str) -> None:
        from docker.errors import DockerException  # noqa: PLC0415

        layers: dict[str, tuple[int, int]] = {}
        try:
            client = make()
            if client is None:
                raise OSError("Docker isn't running.")
            name, tag = image.rsplit(":", 1)
            for event in client.api.pull(name, tag=tag, stream=True, decode=True):
                detail = cast(dict[str, Any], event.get("progressDetail") or {})
                if event.get("status") == "Downloading" and detail.get("total"):
                    layers[str(event.get("id"))] = (int(detail["current"]), int(detail["total"]))
                    with self._lock:
                        self._done = sum(d for d, _ in layers.values())
                        self._total = sum(t for _, t in layers.values())
        except (DockerException, OSError) as error:
            logger.warning("the worker image didn't download: %s", error)
            with self._lock:
                self._error = "The download didn't finish. Check your connection and try again."
        finally:
            with self._lock:
                self._running = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "downloading": self._running, "downloaded": self._done, "total": self._total,
                "error": self._error,
            }  # fmt: skip


download = ImageDownload()


def status(settings: Settings, size: Callable[[str], int | None] = image_size) -> dict[str, Any]:
    """What Settings shows: is this possible here, and what's the next step."""
    image = image_for(settings)
    client = connect() if image else None
    ready = client is not None and image is not None and has_image(client, image)
    return {
        "on": settings.docker_tasks,
        "available": image is not None,
        "docker_running": client is not None,
        "image_ready": ready,
        "size": size(image) if image and client is not None and not ready else None,
        **download.status(),
    }


def turn_on(settings: Settings) -> str | None:
    """None if tasks can run in Docker now, else why not, in words for the user."""
    image = image_for(settings)
    client = connect()
    if image is None:
        return "This version of Ringkeeper doesn't have a Docker image."
    if client is None:
        return "Docker isn't running. Start Docker Desktop, then try again."
    if not has_image(client, image):
        return "Download the image first."
    with contextlib.closing(client):
        return check(client, image, settings.gateway_internal_url)
