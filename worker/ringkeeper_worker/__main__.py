"""Entrypoint: prepare the workspace, run the agent, report what changed."""

import asyncio
import sys

from ringkeeper_worker import agent, stub
from ringkeeper_worker.checks import run_checks
from ringkeeper_worker.config import ConfigError, WorkerConfig, load
from ringkeeper_worker.emit import emit
from ringkeeper_worker.workspace import Workspace, WorkspaceError


def _run(config: WorkerConfig) -> bool:
    runner = stub.run if config.stub is not None else agent.run
    try:
        return asyncio.run(runner(config))
    except Exception as error:
        emit("ringkeeper.error", message=f"{type(error).__name__}: {error}"[:2000])
        return False


def _finish(config: WorkerConfig, workspace: Workspace) -> None:
    try:
        message = f"ringkeeper: task {config.task_id} session {config.session_ordinal}"
        emit("ringkeeper.workspace", **workspace.finish(message).as_event())
    except WorkspaceError as error:
        emit("ringkeeper.error", message=str(error))
        return
    if config.checks:
        run_checks(config.checks, config.workspace)
        diff = workspace.diff()
        if diff is not None:
            emit("ringkeeper.diff", text=diff)


def main() -> int:
    try:
        config = load()
    except ConfigError as error:
        emit("ringkeeper.error", message=str(error))
        return 2
    emit(
        "ringkeeper.start",
        task_id=config.task_id,
        session_ordinal=config.session_ordinal,
        resume=config.resume,
        stub=config.stub is not None,
    )
    if config.role == "architect":
        ok = _run(config)  # the workspace is mounted read-only: nothing to commit
    else:
        workspace = Workspace(config.workspace, config.branch)
        try:
            workspace.start()
        except WorkspaceError as error:
            emit("ringkeeper.error", message=str(error))
            return 3
        try:
            ok = _run(config)
        finally:
            _finish(config, workspace)
    emit("ringkeeper.end", ok=ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
