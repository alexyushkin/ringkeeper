"""A stand-in agent for integration tests: no LLM, only facts about its own container.

It reports what a worker can see (mounts, which env vars exist, whether the
master key, data dir or Docker socket are reachable), calls the gateway like
a real agent would, optionally writes to the workspace and sleeps, then emits
a result message shaped like the SDK's. It never reports env var values.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from ringkeeper_worker.config import WorkerConfig
from ringkeeper_worker.emit import emit

FORBIDDEN_PATHS = (
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/var/lib/ringkeeper",
    "/var/lib/ringkeeper/master.key",
    "/.ringkeeper",
)
PSEUDO_MOUNTS = ("/proc", "/sys", "/dev")


def mount_points() -> list[str]:
    if not Path("/proc/self/mountinfo").exists():  # not Linux: a local process (desktop)
        return []
    points: list[str] = []
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        point = line.split()[4]
        if not point.startswith(PSEUDO_MOUNTS):
            points.append(point)
    return sorted(set(points))


def current_uid() -> int:
    if not Path("/proc/self/status").exists():
        return -1
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[1])
    return -1


def find_files(name: str, roots: tuple[str, ...] = ("/",)) -> list[str]:
    found: list[str] = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames if not os.path.join(dirpath, d).startswith(PSEUDO_MOUNTS)
            ]
            if name in filenames:
                found.append(os.path.join(dirpath, name))
    return found


async def call_gateway(
    url: str, token: str, tool: str | None = None, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    import httpx2  # noqa: PLC0415 (only the stub needs these)
    from mcp import ClientSession  # noqa: PLC0415
    from mcp.client.streamable_http import streamable_http_client  # noqa: PLC0415

    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with (
            httpx2.AsyncClient(headers=headers, timeout=15) as http,
            streamable_http_client(url, http_client=http) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            report: dict[str, Any] = {"ok": True, "tools": sorted(t.name for t in tools.tools)}
            if tool is not None:
                result = await session.call_tool(tool, args or {})
                report["called"] = tool
                # The field name differs across mcp versions (is_error / isError).
                report["is_error"] = bool(
                    getattr(result, "is_error", None) or getattr(result, "isError", False)
                )
                report["text"] = " ".join(getattr(c, "text", "") for c in result.content)[:500]
            return report
    except Exception as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"[:300]}


def tamper(workspace: Path) -> None:
    """What a hijacked agent might do: plant code that git would run on the host,
    and leave a process behind that keeps planting it."""
    git = workspace / ".git"
    hook = git / "hooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\ntouch /workspace/HOOK-RAN\n", encoding="utf-8")
    hook.chmod(0o755)
    with (git / "config").open("a", encoding="utf-8") as config:
        config.write("[core]\n\tfsmonitor = touch /workspace/FSMONITOR-RAN\n")
    (git / "info").mkdir(exist_ok=True)
    (git / "info" / "attributes").write_text("* filter=evil\n", encoding="utf-8")
    # Python, not sh, so it runs anywhere a worker runs (a container, Windows, macOS).
    late = git / "hooks" / "pre-commit"
    loop = (
        "import pathlib, time\n"
        f"p = pathlib.Path({str(late)!r})\n"
        "while True:\n"
        "    try: p.write_text('#!/bin/sh\\ntouch LATE-HOOK-RAN\\n')\n"
        "    except OSError: pass\n"
        "    time.sleep(0.05)\n"
    )
    subprocess.Popen([sys.executable, "-c", loop], start_new_session=True)  # noqa: S603
    nested = workspace / "vendor" / "lib" / ".git" / "hooks"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "post-checkout").write_text("#!/bin/sh\n", encoding="utf-8")


async def reachable(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
    except (OSError, TimeoutError):
        return False
    writer.close()
    return True


async def run(config: WorkerConfig) -> bool:
    # Options can be given per role ({"architect": {...}, "worker": {...}}), and a
    # gateway tool call per session ordinal ({"calls": {"1": {"tool", "args"}}}).
    raw: dict[str, Any] = config.stub or {}
    stub: dict[str, Any] = {**raw, **raw.get(config.role, {})}
    call: dict[str, Any] = stub.get("calls", {}).get(str(config.session_ordinal), {})
    emit(
        "stub.environment",
        uid=current_uid(),
        mounts=mount_points(),
        env_names=sorted(os.environ),
        forbidden_present=[p for p in FORBIDDEN_PATHS if os.path.exists(p)],
        # Searching the whole disk only makes sense inside a container.
        master_keys=find_files("master.key") if mount_points() else [],
        workspace_writable=os.access(config.workspace, os.W_OK),
        workspace=config.workspace,
        home=os.environ.get("HOME"),
        claude_config_dir=os.environ.get("CLAUDE_CONFIG_DIR"),
    )
    if stub.get("leave_behind"):
        # A process that outlives the agent, like a dev server it forgot to stop.
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True
        )
        emit("stub.left_behind", pid=child.pid)
    for host, port in stub.get("probe", []):
        emit("stub.probe", host=host, port=port, reachable=await reachable(str(host), int(port)))
    for name, url in config.mcp_servers.items():
        report = await call_gateway(url, config.gateway_token, call.get("tool"), call.get("args"))
        emit("stub.gateway", server=name, **report)
    if stub.get("tamper"):
        tamper(Path(config.workspace))
    for relpath, content in stub.get("write", {}).items():
        target = Path(config.workspace) / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if seconds := float(stub.get("sleep", 0)):
        await asyncio.sleep(seconds)
    if (question := stub.get("ask_user")) and not config.resume:
        emit("ringkeeper.ask_user", question=str(question))
    session_id = config.resume or str(uuid.uuid4())
    subtype = str(stub.get("subtype", "success"))
    emit(
        "sdk.result",
        message={
            "_kind": "ResultMessage",
            "subtype": subtype,
            "is_error": subtype != "success",
            "num_turns": int(stub.get("turns", 1)),
            "session_id": session_id,
            "total_cost_usd": stub.get("cost_usd"),
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "model_usage": None,
            "result": "stub done",
            "model": config.model,
        },
    )
    return subtype == "success"
