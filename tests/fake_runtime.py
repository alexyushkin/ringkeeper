"""A container runtime that plays a script instead of starting Docker."""

import json
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ringkeeper.workers.runtime import WorkerRuntimeError, WorkerSpec


def line(event_type: str, **data: Any) -> str:
    return json.dumps({"type": event_type, **data})


def result_line(
    *,
    subtype: str = "success",
    turns: int = 3,
    cost: float | None = 0.0123,
    session_id: str = "sdk-1",
) -> str:
    return line(
        "sdk.result",
        message={
            "_kind": "ResultMessage",
            "subtype": subtype,
            "is_error": subtype != "success",
            "num_turns": turns,
            "session_id": session_id,
            "total_cost_usd": cost,
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "model_usage": {"claude-test": {"costUSD": cost}},
        },
    )


def workspace_line(
    changed: list[str], *, git: bool = True, branch: str | None = "ringkeeper/x"
) -> str:
    return line(
        "ringkeeper.workspace", git=git, changed_files=changed, branch=branch if git else None,
        commit="abc123" if git else None,
    )  # fmt: skip


@dataclass
class Script:
    before: list[str] = field(default_factory=list[str])  # emitted at once
    delay: float = 0.0  # then wait this long (a kill ends the wait)
    after: list[str] = field(default_factory=lambda: [result_line(), workspace_line([])])
    exit_code: int = 0


def _default_script(_spec: WorkerSpec) -> Script:
    return Script()


class FakeRuntime:
    def __init__(self, script: Callable[[WorkerSpec], Script] | None = None) -> None:
        self.script: Callable[[WorkerSpec], Script] = script or _default_script
        self.started: list[WorkerSpec] = []
        self.removed: list[str] = []
        self.killed: list[str] = []
        self.volumes: set[str] = set()  # state volumes, as Docker would list them
        self.peak = 0
        self._running = 0
        self._lock = threading.Lock()
        self._scripts: dict[str, Script] = {}
        self._kill_events: dict[str, threading.Event] = {}

    def start(self, spec: WorkerSpec) -> str:
        if spec.image == "missing-image":
            raise WorkerRuntimeError("worker image missing-image not found")
        container_id = uuid.uuid4().hex
        with self._lock:
            self.started.append(spec)
            self._scripts[container_id] = self.script(spec)
            self._kill_events[container_id] = threading.Event()
            self._running += 1
            self.peak = max(self.peak, self._running)
        return container_id

    def lines(self, container_id: str) -> Iterator[str]:
        script = self._scripts[container_id]
        killed = self._kill_events[container_id]
        yield from script.before
        if script.delay and killed.wait(script.delay):
            return
        if killed.is_set():
            return
        yield from script.after

    def wait(self, container_id: str) -> int:
        with self._lock:
            self._running -= 1
        return (
            137
            if self._kill_events[container_id].is_set()
            else self._scripts[container_id].exit_code
        )

    def kill(self, container_id: str) -> None:
        self.killed.append(container_id)
        self._kill_events[container_id].set()

    def remove(self, container_id: str) -> None:
        self.removed.append(container_id)

    def running_workers(self) -> dict[str, str]:
        return {}

    def state_volumes(self) -> list[str]:
        return sorted(self.volumes)

    def remove_volume(self, name: str) -> bool:
        if name not in self.volumes:
            return False
        self.volumes.discard(name)
        return True

    @property
    def running(self) -> int:
        with self._lock:
            return self._running
