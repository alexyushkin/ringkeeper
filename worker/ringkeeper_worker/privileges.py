"""Who runs what inside a worker container.

The runner (this package's entrypoint) is root with only SETUID, SETGID, KILL
and DAC_READ_SEARCH. Everything that runs the agent's or the project's code
runs as the `worker` user (uid 1000): the agent itself, git, and the checks.
So after the agent, the runner can kill anything the agent left running,
and put the repository's git config and hooks back, from a process the
agent can't touch.

Outside a container (tests on a developer machine) the runner isn't root,
and everything simply runs as the current user.
"""

import json
import os
import signal
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

WORKER_UID = 1000
WORKER_GID = 1000
WORKER_HOME = "/home/worker"

# POSIX-only calls (fork, setuid, ...): the worker runs only on Linux, but the
# type checker also sees this module from the tests' Windows environment.
_os: Any = os
_signal: Any = signal


# Set by Ringkeeper's process runtime (desktop mode): this runner is a local
# process, not the worker container, even if it happens to run as root.
LOCAL_MARKER = "RK_RUN_ID"


def is_root() -> bool:
    """Root in the worker container: the one case where work drops to `worker`."""
    if LOCAL_MARKER in os.environ:
        return False
    return hasattr(os, "geteuid") and _os.geteuid() == 0


def as_worker_kwargs() -> dict[str, Any]:
    """subprocess.run arguments that drop to the worker user (nothing if not root)."""
    if not is_root():
        return {}
    return {"user": WORKER_UID, "group": WORKER_GID, "extra_groups": []}


def _drop() -> None:
    _os.setgroups([])
    _os.setgid(WORKER_GID)
    _os.setuid(WORKER_UID)
    os.environ["HOME"] = WORKER_HOME


def as_worker[T](fn: Callable[[], T]) -> T:
    """Run fn as the worker user in a forked child; its JSON-able result comes back.

    The child can't change anything in this process: the runner's own state
    (like the git snapshot) stays out of the agent's reach.
    """
    if not is_root():
        return fn()
    read_end, write_end = os.pipe()
    pid = _os.fork()
    if pid == 0:  # the child
        os.close(read_end)
        try:
            _drop()
            payload = json.dumps({"result": fn()})
        except BaseException as error:
            payload = json.dumps({"error": f"{type(error).__name__}: {error}"})
        data = payload.encode()
        while data:
            data = data[os.write(write_end, data) :]
        os._exit(0)
    os.close(write_end)
    chunks: list[bytes] = []
    with os.fdopen(read_end, "rb") as pipe:
        while chunk := pipe.read(65536):
            chunks.append(chunk)
    _os.waitpid(pid, 0)
    outcome = json.loads(b"".join(chunks) or b'{"error": "the worker process died"}')
    if "error" in outcome:
        raise RuntimeError(outcome["error"])
    return outcome["result"]


def _worker_pids() -> Iterator[int]:
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            status = Path(f"/proc/{entry}/status").read_text(encoding="utf-8")
        except OSError:
            continue
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        if fields.get("State", "").strip().startswith("Z"):
            continue  # already dead, waiting to be reaped
        if WORKER_UID in (int(uid) for uid in fields.get("Uid", "").split()):
            yield int(entry)


def _reap() -> None:
    # Orphans are re-parented to us (PID 1 in the container): collect them.
    try:
        while _os.waitpid(-1, _os.WNOHANG)[0] > 0:
            pass
    except ChildProcessError:
        pass


def kill_strays() -> int:
    """Kill every process still running as the worker user (an agent's leftovers)."""
    if not is_root():
        return 0
    killed = 0
    for _ in range(50):
        pids = list(_worker_pids())
        if not pids:
            break
        for pid in pids:
            try:
                os.kill(pid, _signal.SIGKILL)
                killed += 1
            except ProcessLookupError:
                pass
        time.sleep(0.05)
        _reap()
    _reap()
    return killed
