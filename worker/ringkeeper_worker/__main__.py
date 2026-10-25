"""Entrypoint: prepare the workspace, run the agent, report what changed.

The runner is root (see privileges.py); the agent, git and checks run as
the worker user. After the agent (and again after the checks), whatever it
left running is killed and the repository's git config and hooks are put
back (gitguard.py) before anything else touches the workspace.
"""

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import Any

from ringkeeper_worker import agent, gitguard, stub
from ringkeeper_worker.checks import run_checks
from ringkeeper_worker.config import TASK_ENV, TOKEN_ENV, ConfigError, WorkerConfig, load
from ringkeeper_worker.emit import emit
from ringkeeper_worker.privileges import as_worker, kill_strays
from ringkeeper_worker.workspace import Workspace, WorkspaceError


def _run(config: WorkerConfig) -> bool:
    try:
        if config.stub is not None:
            # The stand-in agent runs as the worker user, like the real one.
            return bool(as_worker(lambda: asyncio.run(stub.run(config))))
        return asyncio.run(agent.run(config))
    except Exception as error:
        emit("ringkeeper.error", message=f"{type(error).__name__}: {error}"[:2000])
        return False


class Guard:
    """The git files and nested repositories as they were before the agent."""

    def __init__(self, root: str) -> None:
        self.root = root
        self.git = gitguard.capture(root)
        self.nested = set(gitguard.nested_repos(root))
        self.had_git = (Path(root) / ".git").exists()

    def protect(self) -> None:
        """Kill leftovers, then undo changes to .git config, hooks and info."""
        kill_strays()
        git = Path(self.root) / ".git"
        if self.git is not None:
            if not git.is_dir() or git.is_symlink():
                self._set_aside(git)
            elif changed := gitguard.differences(self.git, self.root):
                saved: dict[str, Any] = self.git
                try:
                    as_worker(lambda: gitguard.restore(saved, self.root))
                    emit("ringkeeper.git_restored", paths=changed)
                except RuntimeError as error:
                    emit("ringkeeper.error", message=f"couldn't restore .git: {error}"[:2000])
        elif not self.had_git and git.exists():
            self._warn([".git"])
        if new := sorted(set(gitguard.nested_repos(self.root)) - self.nested):
            self._warn(new)

    def _set_aside(self, git: Path) -> None:
        aside = git.with_name(".git.replaced-by-agent")
        with contextlib.suppress(RuntimeError):
            as_worker(lambda: os.replace(git, aside))
        emit(
            "ringkeeper.error",
            message=f"the agent replaced .git; moved what it left to {aside.name}",
        )

    @staticmethod
    def _warn(paths: list[str]) -> None:
        emit(
            "ringkeeper.warning",
            message="new git repositories in the workspace: "
            + ", ".join(paths)
            + ". Check their hooks and config before running git in them.",
        )


def _finish(config: WorkerConfig, workspace: Workspace, guard: Guard) -> None:
    guard.protect()
    try:
        message = f"ringkeeper: task {config.task_id} session {config.session_ordinal}"
        emit("ringkeeper.workspace", **workspace.finish(message).as_event())
    except WorkspaceError as error:
        emit("ringkeeper.error", message=str(error))
    if config.checks:
        # Always run, even if committing failed: a broken repository mustn't skip them.
        run_checks(config.checks, config.workspace)
        guard.protect()  # the checks ran project code too
        diff = workspace.diff()
        if diff is not None:
            emit("ringkeeper.diff", text=diff)


def main() -> int:
    try:
        config = load()
    except ConfigError as error:
        emit("ringkeeper.error", message=str(error))
        return 2
    # Not inherited by the agent or the checks (the token reaches the agent only
    # in its MCP config).
    os.environ.pop(TASK_ENV, None)
    os.environ.pop(TOKEN_ENV, None)
    emit(
        "ringkeeper.start",
        task_id=config.task_id,
        session_ordinal=config.session_ordinal,
        resume=config.resume,
        stub=config.stub is not None,
    )
    if config.role == "architect":
        ok = _run(config)  # the workspace is mounted read-only: nothing to commit
        kill_strays()
    else:
        workspace = Workspace(config.workspace, config.branch)
        try:
            workspace.start()
        except WorkspaceError as error:
            emit("ringkeeper.error", message=str(error))
            return 3
        guard = Guard(config.workspace)
        try:
            ok = _run(config)
        finally:
            _finish(config, workspace, guard)
    emit("ringkeeper.end", ok=ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
