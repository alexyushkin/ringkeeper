"""A stand-in agent for integration tests: no LLM, only facts about its own container.

It reports what a worker can see (mounts, which env vars exist, whether the
master key, data dir or Docker socket are reachable), calls the gateway like
a real agent would, optionally writes to the workspace and sleeps, then emits
a result message shaped like the SDK's. It never reports env var values.
"""

import asyncio
import os
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
    points: list[str] = []
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        point = line.split()[4]
        if not point.startswith(PSEUDO_MOUNTS):
            points.append(point)
    return sorted(set(points))


def current_uid() -> int:
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


async def call_gateway(url: str, token: str) -> dict[str, Any]:
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
            return {"ok": True, "tools": sorted(t.name for t in tools.tools)}
    except Exception as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"[:300]}


async def reachable(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
    except (OSError, TimeoutError):
        return False
    writer.close()
    return True


async def run(config: WorkerConfig) -> bool:
    stub: dict[str, Any] = config.stub or {}
    emit(
        "stub.environment",
        uid=current_uid(),
        mounts=mount_points(),
        env_names=sorted(os.environ),
        forbidden_present=[p for p in FORBIDDEN_PATHS if os.path.exists(p)],
        master_keys=find_files("master.key"),
        workspace_writable=os.access(config.workspace, os.W_OK),
    )
    for host, port in stub.get("probe", []):
        emit("stub.probe", host=host, port=port, reachable=await reachable(str(host), int(port)))
    for name, url in config.mcp_servers.items():
        emit("stub.gateway", server=name, **await call_gateway(url, config.gateway_token))
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
