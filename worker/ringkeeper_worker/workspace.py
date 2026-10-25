"""What changed in /workspace, and the git rules.

With a git repo: work happens on the task branch (`ringkeeper/<task-id>`),
which is created from the default branch (origin's HEAD, else main or master)
on the first session and reused by resumes, never from whatever happens to be
checked out. The default branch is never moved: if the agent moved it anyway,
its new tip is kept on `<task branch>-from-<name>` and the branch is put back.
Nothing is pushed. Without a repo: a before/after snapshot gives the changed
paths.

Git runs as the worker user, with a minimal environment (no Claude
credential), never runs hooks or an fsmonitor, and commits with --no-verify:
the repository's own config and hooks are the client's, and gitguard puts
them back if the agent changed them.
"""

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ringkeeper_worker.privileges import WORKER_HOME, as_worker_kwargs, is_root

GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Ringkeeper worker",
    "GIT_AUTHOR_EMAIL": "worker@ringkeeper.invalid",
    "GIT_COMMITTER_NAME": "Ringkeeper worker",
    "GIT_COMMITTER_EMAIL": "worker@ringkeeper.invalid",
}
MAX_SNAPSHOT_FILES = 50_000
SAFE_GIT = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.pager=cat")
GIT_ENV = ("PATH", "LANG", "LC_ALL", "TZ")


class WorkspaceError(Exception):
    pass


@dataclass
class Outcome:
    git: bool
    changed_files: list[str] = field(default_factory=list[str])
    branch: str | None = None
    commit: str | None = None
    protected_branch: str | None = None
    restored: str | None = None  # set if the protected branch had to be put back

    def as_event(self) -> dict[str, object]:
        return {
            "git": self.git,
            "changed_files": self.changed_files,
            "branch": self.branch,
            "commit": self.commit,
            "protected_branch": self.protected_branch,
            "restored": self.restored,
        }


def _git_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in GIT_ENV if k in os.environ}
    env["HOME"] = WORKER_HOME if is_root() else os.environ.get("HOME", "")
    env["GIT_TERMINAL_PROMPT"] = "0"
    return {**env, **GIT_IDENTITY}


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(  # noqa: S603 (fixed argv, no shell)
        ["git", *SAFE_GIT, "-C", str(root), *args],  # noqa: S607
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
        **as_worker_kwargs(),
    )
    if check and result.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    files: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                stat = path.lstat()
            except OSError:
                continue
            files[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
            if len(files) >= MAX_SNAPSHOT_FILES:
                return files
    return files


class Workspace:
    def __init__(self, root: str | Path, branch: str) -> None:
        self.root = Path(root)
        self.branch = branch
        self.git = (self.root / ".git").exists()
        self._before: dict[str, tuple[int, int]] = {}
        self._protected: tuple[str, str] | None = None  # (branch, sha) at start
        self._base: str | None = None

    def _sha(self, ref: str) -> str:
        return _git(self.root, "rev-parse", "--verify", "--quiet", ref, check=False)

    def _default_branch(self) -> tuple[str, str] | None:
        """The repo's default branch and its tip: origin's HEAD if known, else main or master."""
        origin = _git(
            self.root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", check=False
        )
        names = [origin.removeprefix("origin/")] if origin else []
        for name in [*names, "main", "master"]:
            if sha := self._sha(f"refs/heads/{name}"):
                return name, sha
        return None

    def start(self) -> None:
        if not self.git:
            self._before = _snapshot(self.root)
            return
        current = _git(self.root, "rev-parse", "--abbrev-ref", "HEAD")
        exists = self._sha(f"refs/heads/{self.branch}")
        default = self._default_branch()
        if default:
            self._protected = default
        elif current not in ("HEAD", self.branch):  # no default branch: protect what's out
            self._protected = (current, _git(self.root, "rev-parse", "HEAD"))
        if current != self.branch:
            if exists:
                _git(self.root, "switch", self.branch)
            elif default:
                # Always from the default branch, never from whatever was checked out
                # (e.g. an earlier task's branch that hasn't been merged).
                _git(self.root, "switch", "-c", self.branch, default[0])
            else:
                _git(self.root, "switch", "-c", self.branch)
        if default:
            # Where the task branch left the default branch, so the diff is only its own work.
            self._base = _git(self.root, "merge-base", default[1], "HEAD")
        elif self._protected:
            self._base = self._protected[1]
        else:
            self._base = _git(self.root, "rev-parse", "HEAD")
        # Taken after the switch, in git mode too: only files touched during this
        # run get committed, never edits already in the working tree (or line-ending
        # noise from a checkout made with different git settings on the host).
        self._before = _snapshot(self.root)

    def _touched(self) -> list[str]:
        after = _snapshot(self.root)
        return sorted(
            {p for p in after if self._before.get(p) != after[p]}
            | {p for p in self._before if p not in after}
        )

    def finish(self, message: str) -> Outcome:
        if not self.git:
            return Outcome(git=False, changed_files=self._touched())

        outcome = Outcome(git=True, branch=self.branch)
        restored = self._restore_protected()
        current = _git(self.root, "rev-parse", "--abbrev-ref", "HEAD")
        if current != self.branch:
            _git(self.root, "switch", self.branch)
        touched = self._touched()
        for start in range(0, len(touched), 200):
            # -A also stages deletions. Gitignored paths make `add` exit non-zero
            # while still staging the rest, so that is not an error here.
            _git(self.root, "add", "-A", "--", *touched[start : start + 200], check=False)
        staged = _git(self.root, "diff", "--cached", "--name-only")
        if staged:
            _git(self.root, "commit", "-q", "--no-verify", "-m", message)
        outcome.commit = _git(self.root, "rev-parse", self.branch)
        if self._base:
            diff = _git(self.root, "diff", "--no-ext-diff", "--name-only", self._base, self.branch)
            outcome.changed_files = sorted(line for line in diff.splitlines() if line)
        if self._protected:
            outcome.protected_branch = self._protected[0]
        outcome.restored = restored
        return outcome

    def diff(self, limit: int = 60_000) -> str | None:
        """The task branch's cumulative diff against where it started (git only)."""
        if not self.git or not self._base:
            return None
        text = _git(
            self.root, "diff", "--no-color", "--no-ext-diff", self._base, self.branch, check=False
        )
        return text if len(text) <= limit else text[:limit] + "\n… [diff truncated]"

    def _restore_protected(self) -> str | None:
        if not self._protected:
            return None
        name, sha = self._protected
        now = _git(self.root, "rev-parse", f"refs/heads/{name}")
        if now == sha:
            return None
        keep = f"{self.branch}-from-{name}"
        _git(self.root, "branch", "-f", keep, now)
        if _git(self.root, "rev-parse", "--abbrev-ref", "HEAD") == name:
            _git(self.root, "switch", self.branch)
        _git(self.root, "branch", "-f", name, sha)
        return (
            f"{name} was moved to {now[:12]}; restored to {sha[:12]}, moved commits kept on {keep}"
        )
