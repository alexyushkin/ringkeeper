"""Entrypoint: prepare the workspace, run the agent, report what changed."""

import asyncio
import sys

from ringkeeper_worker import agent, stub
from ringkeeper_worker.config import ConfigError, load
from ringkeeper_worker.emit import emit
from ringkeeper_worker.workspace import Workspace, WorkspaceError


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
    workspace = Workspace(config.workspace, config.branch)
    try:
        workspace.start()
    except WorkspaceError as error:
        emit("ringkeeper.error", message=str(error))
        return 3

    runner = stub.run if config.stub is not None else agent.run
    ok = False
    try:
        ok = asyncio.run(runner(config))
    except Exception as error:
        emit("ringkeeper.error", message=f"{type(error).__name__}: {error}"[:2000])
    finally:
        try:
            message = f"ringkeeper: task {config.task_id} session {config.session_ordinal}"
            emit("ringkeeper.workspace", **workspace.finish(message).as_event())
        except WorkspaceError as error:
            emit("ringkeeper.error", message=str(error))
    emit("ringkeeper.end", ok=ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
