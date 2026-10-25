"""Workers as local processes: the desktop app's runtime (no Docker).

Same `ContainerRuntime` interface as DockerRuntime, so the session manager
doesn't change. Each worker is `python -m ringkeeper_worker`, started with:

- only the variables it needs (the manager's allowlist plus what the OS needs
  to run a program), never this process's own environment;
- its own home and Claude config under the task's state directory, so it
  never reads the user's own ~/.claude, settings or MCP servers;
- the project's folder as its workspace, given in its task description;
- a Windows Job Object (memory cap, and everything it starts dies with it or
  with Ringkeeper), or on macOS and Linux its own process group. A process
  that leaves the group (setsid) is still found on Linux by a marker in its
  environment; on macOS it's out of reach (Windows is the supported desktop).

In a container the runner is root: after the agent it kills whatever the
agent left running, then puts the repository's .git config, hooks and info
back. A local runner can't kill what it can't see, so here Ringkeeper itself
takes those files before the worker starts and, once the worker's whole tree
is dead, puts them back (ringkeeper_worker.gitguard, the same code).

What a container gave and this doesn't (docs/DESKTOP_PLAN.md, section 3): the
worker can read whatever the user can, reach the local network, and the
architect's "read-only" workspace is only enforced by its tool list. Its
connection secrets still never reach it: the gateway holds them.
"""

import contextlib
import json
import logging
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import IO, Any, cast

from ringkeeper.errors import RingkeeperError
from ringkeeper.workers.env import STATE_DIR, TASK_ENV
from ringkeeper.workers.runtime import LABEL_SESSION, STATE_PREFIX, WorkerSpec

logger = logging.getLogger(__name__)

WINDOWS = sys.platform == "win32"
# What Windows itself needs for a program (Python, git, node) to start and run.
WINDOWS_ENV = (
    "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
)  # fmt: skip
POSIX_ENV = ("LANG", "LC_ALL", "TZ")
REGISTRY = "workers.json"  # pids of running workers, to clean up after a crash
MARKER = "RK_RUN_ID"  # in every worker process's environment, inherited by its children
# Once the runner has exited and its tree is gone, how long its output may still trickle in.
DRAIN_SECONDS = 2.0
# Its own process group, and no console window popping up.
_WINDOWS_FLAGS = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
    subprocess, "CREATE_NO_WINDOW", 0
)


class ProcessRuntimeError(RingkeeperError):
    pass


def parse_memory(value: str) -> int:
    """'2g', '512m', '1048576' -> bytes."""
    units = {"k": 1024, "m": 1024**2, "g": 1024**3}
    text = value.strip().lower().removesuffix("b")
    if text and text[-1] in units:
        return int(float(text[:-1]) * units[text[-1]])
    return int(text)


def worker_pythonpath() -> str | None:
    """Where ringkeeper_worker lives, if it isn't installed into this Python."""
    spec = find_spec("ringkeeper_worker")
    if spec is None or not spec.submodule_search_locations:
        candidate = Path(__file__).resolve().parents[3] / "worker"  # a source checkout
        return str(candidate) if (candidate / "ringkeeper_worker").is_dir() else None
    return str(Path(next(iter(spec.submodule_search_locations))).parent)


def _gitguard() -> Any:
    """The worker's gitguard module (stdlib only), importable from here too."""
    path = worker_pythonpath()
    if path is not None and path not in sys.path:
        sys.path.append(path)
    return import_module("ringkeeper_worker.gitguard")


@dataclass
class _Worker:
    process: subprocess.Popen[str]
    session_id: str
    workspace: str
    marker: str
    job: "_Job | None" = None
    log: IO[str] | None = field(default=None, repr=False)
    git: dict[str, Any] | None = field(default=None, repr=False)  # .git files before the run
    output: "queue.Queue[str | None]" = field(default_factory=queue.Queue[str | None])
    exited: float | None = None  # monotonic time the runner's tree was gone


class ProcessRuntime:
    def __init__(
        self,
        state_root: Path,
        *,
        tools_path: list[str] | None = None,
        python: str | None = None,
    ) -> None:
        self.state_root = state_root
        self.tools_path = tools_path or []  # bundled git and such, put first on PATH
        self.python = python or sys.executable
        self._workers: dict[str, _Worker] = {}
        self._lock = threading.Lock()
        state_root.mkdir(parents=True, exist_ok=True)

    # --- Paths and environment ---

    def state_dir(self, name: str) -> Path:
        if not name.startswith(STATE_PREFIX) or any(c in name for c in "/\\:") or ".." in name:
            raise ProcessRuntimeError(f"not a worker state name: {name!r}")
        return self.state_root / name

    def environment(self, spec: WorkerSpec) -> dict[str, str]:
        state = self.state_dir(spec.state_volume)
        home = state / "home"
        env: dict[str, str] = {}
        for name in WINDOWS_ENV if WINDOWS else POSIX_ENV:
            if name in os.environ:
                env[name] = os.environ[name]
        env["PATH"] = os.pathsep.join([*self.tools_path, os.environ.get("PATH", "")])
        # Its own home: the user's ~/.claude, ~/.gitconfig and caches stay out of reach.
        env["HOME"] = str(home)
        if WINDOWS:
            env |= {
                "USERPROFILE": str(home),
                "APPDATA": str(home / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(home / "AppData" / "Local"),
                "TEMP": str(state / "tmp"),
                "TMP": str(state / "tmp"),
            }
        else:
            env["TMPDIR"] = str(state / "tmp")
        env |= {"PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        env[MARKER] = spec.name
        if pythonpath := worker_pythonpath():
            env["PYTHONPATH"] = pythonpath
        for name, value in spec.env.items():
            # The manager describes a container (/state, /workspace): map it here.
            inside = value.removeprefix(STATE_DIR + "/")
            env[name] = str(state / inside) if inside != value else value
        task: dict[str, Any] = json.loads(env[TASK_ENV])
        task["workspace"] = spec.workspace
        env[TASK_ENV] = json.dumps(task, separators=(",", ":"))
        return env

    # --- ContainerRuntime ---

    def start(self, spec: WorkerSpec) -> str:
        workspace = Path(spec.workspace)
        if not workspace.is_dir():
            raise ProcessRuntimeError(f"the project's folder doesn't exist: {workspace}")
        env = self.environment(spec)
        git = _gitguard().capture(workspace)
        state = self.state_dir(spec.state_volume)
        for path in (state / "home", state / "tmp", state / "claude"):
            path.mkdir(parents=True, exist_ok=True)
        log = (state / f"{spec.name}.log").open("a", encoding="utf-8")
        flags: dict[str, Any] = (
            {"creationflags": _WINDOWS_FLAGS} if WINDOWS else {"start_new_session": True}
        )
        try:
            process = subprocess.Popen(  # noqa: S603 (our own runner, fixed argv)
                [self.python, "-m", "ringkeeper_worker"],
                cwd=workspace,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                encoding="utf-8",
                errors="replace",
                **flags,
            )
        except OSError as error:
            log.close()
            raise ProcessRuntimeError(f"cannot start worker: {error}") from None
        job = _Job.wrap(process, parse_memory(spec.memory)) if WINDOWS else None
        session = spec.labels.get(LABEL_SESSION, "")
        worker = _Worker(process, session, spec.workspace, spec.name, job, log, git)
        with self._lock:
            self._workers[spec.name] = worker
            self._save_registry()
        # As in a container: when the runner exits, whatever it left running goes
        # too. Its output is read on a thread of its own, so a process that kept
        # a copy of the pipe can't keep the session open.
        threading.Thread(
            target=_pump, args=(worker,), daemon=True, name=f"rk-read-{spec.name}"
        ).start()
        threading.Thread(
            target=_reap_when_done, args=(worker,), daemon=True, name=f"rk-reap-{spec.name}"
        ).start()
        return spec.name

    def lines(self, container_id: str) -> Iterator[str]:
        worker = self._get(container_id)
        if worker is None:
            return
        while True:
            try:
                line = worker.output.get(timeout=0.25)
            except queue.Empty:
                if worker.exited is not None and time.monotonic() - worker.exited > DRAIN_SECONDS:
                    return  # the runner is gone; something it started still holds the pipe
                continue
            if line is None:
                return
            yield line.rstrip("\r\n")

    def wait(self, container_id: str) -> int:
        worker = self._get(container_id)
        return -1 if worker is None else worker.process.wait()

    def kill(self, container_id: str) -> None:
        worker = self._get(container_id)
        if worker is not None:
            _kill_tree(worker)
            return
        # A worker from before a restart (macOS, Linux; Windows jobs die with us).
        entry = self._load_registry().get(container_id)
        if entry is not None:
            _kill_group(int(entry["pid"]))

    def remove(self, container_id: str) -> None:
        with self._lock:
            worker = self._workers.pop(container_id, None)
            self._save_registry()
        if worker is None:
            return
        # Whatever the agent left running goes too, even once the runner has exited.
        _kill_tree(worker)
        with contextlib.suppress(subprocess.TimeoutExpired):
            worker.process.wait(10)
        if worker.job is not None:
            worker.job.close()
        self._restore_git(worker)
        if worker.log is not None:
            worker.log.close()

    def running_workers(self) -> dict[str, str]:
        found = {
            name: entry.get("session", "")
            for name, entry in self._load_registry().items()
            if _alive(int(entry["pid"]))
        }
        with self._lock:
            found |= {n: w.session_id for n, w in self._workers.items() if w.process.poll() is None}
        return found

    def state_volumes(self) -> list[str]:
        return sorted(
            p.name
            for p in self.state_root.iterdir()
            if p.is_dir() and p.name.startswith(STATE_PREFIX)
        )

    def remove_volume(self, name: str) -> bool:
        path = self.state_dir(name)
        if not path.exists():
            return False
        try:
            shutil.rmtree(path)
        except OSError:  # a running worker still has files open (Windows)
            return False
        return True

    # --- Internals ---

    def _restore_git(self, worker: _Worker) -> None:
        if worker.git is None:
            return
        gitguard = _gitguard()
        try:
            if changed := gitguard.differences(worker.git, worker.workspace):
                gitguard.restore(worker.git, worker.workspace)
                logger.warning(
                    "put back .git files a worker changed in %s: %s",
                    worker.workspace, ", ".join(changed),
                )  # fmt: skip
        except OSError as error:
            logger.error("couldn't check .git in %s after a worker: %s", worker.workspace, error)

    def _get(self, container_id: str) -> _Worker | None:
        with self._lock:
            return self._workers.get(container_id)

    def _registry_path(self) -> Path:
        return self.state_root / REGISTRY

    def _load_registry(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._registry_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return cast(dict[str, dict[str, Any]], data) if isinstance(data, dict) else {}

    def _save_registry(self) -> None:
        if WINDOWS:
            return  # a Job Object dies with this process, and its workers with it
        data = {
            name: {"pid": w.process.pid, "session": w.session_id}
            for name, w in self._workers.items()
        }
        tmp = self._registry_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self._registry_path())


def _alive(pid: int) -> bool:
    if WINDOWS:
        return False  # registry unused there
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_group(pid: int, *, verify: bool = True) -> None:
    """Kill a worker's whole process group (it leads its own session).

    `verify` checks the leader is still ours first (a pid from before a restart
    could have been reused). A group whose leader has exited can't be checked
    that way, so the check is skipped only for workers we still track.
    """
    if WINDOWS:
        return
    killpg: Any = getattr(os, "killpg")  # noqa: B009 (POSIX only)
    getpgid: Any = getattr(os, "getpgid")  # noqa: B009
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if not verify or getpgid(pid) == pid:
            killpg(pid, getattr(signal, "SIGKILL"))  # noqa: B009


def _pump(worker: _Worker) -> None:
    stream = worker.process.stdout
    try:
        if stream is not None:
            for line in stream:
                worker.output.put(line)
    except (OSError, ValueError):
        pass
    finally:
        worker.output.put(None)


def _reap_when_done(worker: _Worker) -> None:
    worker.process.wait()
    _kill_tree(worker)
    worker.exited = time.monotonic()


def _marked_pids(marker: str) -> list[int]:
    """Linux: processes (ours) whose environment carries this worker's marker."""
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    needle = f"{MARKER}={marker}".encode()
    found: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        with contextlib.suppress(OSError):
            if needle in (entry / "environ").read_bytes().split(b"\0"):
                found.append(int(entry.name))
    return found


def _kill_tree(worker: _Worker) -> None:
    if worker.job is not None:
        worker.job.terminate()
    elif not WINDOWS:
        _kill_group(worker.process.pid, verify=False)
        for pid in _marked_pids(worker.marker):  # ones that left the group (setsid)
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, getattr(signal, "SIGKILL"))  # noqa: B009
    with contextlib.suppress(OSError):
        worker.process.kill()


class _Job:
    """A Windows Job Object: kills the whole tree at once, caps its memory, and
    takes the worker down with Ringkeeper if Ringkeeper exits (kill on close)."""

    KILL_ON_JOB_CLOSE = 0x2000
    JOB_MEMORY = 0x200
    EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self, handle: int) -> None:
        self.handle = handle

    @classmethod
    def wrap(cls, process: subprocess.Popen[str], memory_bytes: int) -> "_Job":
        import ctypes  # noqa: PLC0415 (Windows only)
        from ctypes import wintypes  # noqa: PLC0415

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32: Any = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ProcessRuntimeError("couldn't create a Windows job for the worker")
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = cls.KILL_ON_JOB_CLOSE | cls.JOB_MEMORY
        limits.JobMemoryLimit = memory_bytes
        job = cls(handle)
        set_info = kernel32.SetInformationJobObject(
            wintypes.HANDLE(handle), cls.EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits), ctypes.sizeof(limits),
        )  # fmt: skip
        process_handle = int(getattr(process, "_handle"))  # noqa: B009 (Popen's process handle)
        assigned = set_info and kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(handle), wintypes.HANDLE(process_handle)
        )
        if not assigned:
            process.kill()
            job.close()
            raise ProcessRuntimeError(
                "couldn't put the worker in a Windows job "
                f"(error {getattr(ctypes, 'get_last_error')()})"  # noqa: B009 (Windows only)
            )
        return job

    def terminate(self) -> None:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        if self.handle:
            kernel32: Any = getattr(ctypes, "WinDLL")("kernel32")  # noqa: B009
            kernel32.TerminateJobObject(wintypes.HANDLE(self.handle), 1)

    def close(self) -> None:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        if self.handle:
            kernel32: Any = getattr(ctypes, "WinDLL")("kernel32")  # noqa: B009
            kernel32.CloseHandle(wintypes.HANDLE(self.handle))
            self.handle = 0
