"""Starting and stopping worker containers.

`ContainerRuntime` is what the session manager needs; `DockerRuntime` talks to
the Docker daemon (the api service is the only one that mounts its socket,
which makes it root-equivalent on the host). Tests use a fake runtime.
"""

import contextlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Protocol, cast

from ringkeeper.errors import RingkeeperError
from ringkeeper.workers.env import STATE_DIR, WORKSPACE

LABEL = "ringkeeper.role"
LABEL_SESSION = "ringkeeper.session"
LABEL_TASK = "ringkeeper.task"
WORKER_UID = "1000:1000"

_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


class WorkerRuntimeError(RingkeeperError):
    pass


def daemon_path(path: str) -> str:
    """A host path as the Docker daemon sees it.

    Docker Desktop's daemon runs in a Linux VM where the Windows drives live
    under /run/desktop/mnt/host/<drive>. The docker CLI translates for you; an
    API call from inside a container doesn't, so we do.
    """
    if _WINDOWS_PATH.match(path):
        windows = PureWindowsPath(path)
        drive = windows.drive.rstrip(":").lower()
        rest = PurePosixPath(*windows.parts[1:])
        return f"/run/desktop/mnt/host/{drive}/{rest}"
    return path


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    image: str
    env: dict[str, str] = field(repr=False)
    workspace: str  # host path, as given in the registry
    state_volume: str
    network: str
    labels: dict[str, str] = field(default_factory=dict[str, str])
    memory: str = "2g"
    extra_hosts: dict[str, str] = field(default_factory=dict[str, str])
    workspace_read_only: bool = False  # architect sessions read, never write


class ContainerRuntime(Protocol):
    def start(self, spec: WorkerSpec) -> str: ...
    def lines(self, container_id: str) -> Iterator[str]: ...
    def wait(self, container_id: str) -> int: ...
    def kill(self, container_id: str) -> None: ...
    def remove(self, container_id: str) -> None: ...
    def running_workers(self) -> dict[str, str]: ...  # container id -> session id


class DockerRuntime:
    def __init__(self, client: Any = None) -> None:
        import docker  # noqa: PLC0415 (only the api service needs it)

        self._client: Any = client or docker.from_env()

    def start(self, spec: WorkerSpec) -> str:
        from docker.errors import APIError, ImageNotFound  # noqa: PLC0415
        from docker.types import Mount  # noqa: PLC0415

        mounts = [
            # The project's workspace is the only host directory a worker sees.
            Mount(
                WORKSPACE,
                daemon_path(spec.workspace),
                type="bind",
                read_only=spec.workspace_read_only,
            ),
            Mount(STATE_DIR, spec.state_volume, type="volume"),
        ]
        try:
            container = self._client.containers.run(
                spec.image,
                name=spec.name,
                detach=True,
                environment=spec.env,
                mounts=mounts,
                network=spec.network,
                user=WORKER_UID,
                read_only=True,
                tmpfs={"/tmp": "", "/home/worker": "uid=1000,gid=1000"},  # noqa: S108
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                pids_limit=512,
                mem_limit=spec.memory,
                # Don't let a worker reach host services by name (Postgres is
                # published on the host's loopback for the CLI).
                extra_hosts={"host.docker.internal": "127.0.0.1", **spec.extra_hosts},
                labels={LABEL: "worker", **spec.labels},
            )
        except ImageNotFound:
            raise WorkerRuntimeError(
                f"worker image {spec.image} not found; build it with "
                "`docker build -t ringkeeper-worker worker/`"
            ) from None
        except APIError as error:
            raise WorkerRuntimeError(f"cannot start worker: {error.explanation or error}") from None
        return cast(str, container.id)

    def lines(self, container_id: str) -> Iterator[str]:
        container = self._client.containers.get(container_id)
        buffer = b""
        for chunk in container.logs(stream=True, follow=True, stdout=True, stderr=False):
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                yield line.decode("utf-8", errors="replace")
        if buffer:
            yield buffer.decode("utf-8", errors="replace")

    def wait(self, container_id: str) -> int:
        result = self._client.containers.get(container_id).wait()
        return int(result.get("StatusCode", -1))

    def kill(self, container_id: str) -> None:
        from docker.errors import APIError, NotFound  # noqa: PLC0415

        with contextlib.suppress(NotFound, APIError):  # already gone or already stopped
            self._client.containers.get(container_id).kill()

    def remove(self, container_id: str) -> None:
        from docker.errors import NotFound  # noqa: PLC0415

        with contextlib.suppress(NotFound):
            self._client.containers.get(container_id).remove(force=True)

    def running_workers(self) -> dict[str, str]:
        containers = self._client.containers.list(filters={"label": f"{LABEL}=worker"})
        return {c.id: c.labels.get(LABEL_SESSION, "") for c in containers}
