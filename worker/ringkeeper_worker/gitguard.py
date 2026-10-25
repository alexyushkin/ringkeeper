"""Keep the agent from planting code that runs on the host.

The workspace is the client's real repository, bind-mounted from the host.
Git runs hooks from .git/hooks and commands named in .git/config
(core.hooksPath, core.fsmonitor, aliases, filters...), and .git/info holds
attributes that pick filters. An agent that wrote any of these would get its
code run on the host the next time someone uses git there.

So the runner takes these files before the agent starts and puts them back
afterwards, exactly as they were, and reports what it undid. New `.git`
folders elsewhere in the workspace are reported too: don't run git in them
before checking them.
"""

import base64
import contextlib
import os
from pathlib import Path
from typing import Any

GUARDED_DIRS = ("hooks", "info")
MAX_NESTED = 20

# rel path under .git -> [mode, base64 content]; JSON-able, to cross the process boundary.
Files = dict[str, list[Any]]


def _read(path: Path) -> list[Any]:
    return [path.lstat().st_mode & 0o777, base64.b64encode(path.read_bytes()).decode()]


def capture(root: str | Path) -> dict[str, Any] | None:
    """The guarded files, or None without a .git folder."""
    git = Path(root) / ".git"
    if not git.is_dir() or git.is_symlink():
        return None
    files: Files = {}
    config = git / "config"
    if config.is_file() and not config.is_symlink():
        files["config"] = _read(config)
    for sub in GUARDED_DIRS:
        base = git / sub
        if not base.is_dir() or base.is_symlink():
            continue
        for dirpath, _, filenames in os.walk(base):
            for name in filenames:
                path = Path(dirpath) / name
                if path.is_file() and not path.is_symlink():
                    files[path.relative_to(git).as_posix()] = _read(path)
    return {"files": files}


def differences(before: dict[str, Any], root: str | Path) -> list[str]:
    after = capture(root)
    if after is None:
        return [".git"]
    old, new = before["files"], after["files"]
    changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
    git = Path(root) / ".git"
    for sub in GUARDED_DIRS:  # symlinks the capture skips
        base = git / sub
        if base.is_symlink():
            changed.add(sub)
        elif base.is_dir():
            for dirpath, dirnames, filenames in os.walk(base):
                for name in [*dirnames, *filenames]:
                    if (Path(dirpath) / name).is_symlink():
                        changed.add((Path(dirpath) / name).relative_to(git).as_posix())
    if (git / "config").is_symlink():
        changed.add("config")
    return sorted(changed)


def restore(before: dict[str, Any], root: str | Path) -> None:
    """Put the guarded files back exactly (run as the worker user)."""
    git = Path(root) / ".git"
    old: Files = before["files"]
    config = git / "config"
    if config.is_symlink() or (config.exists() and not config.is_file()):
        config.unlink()
    for sub in GUARDED_DIRS:
        base = git / sub
        if base.is_symlink() or (base.exists() and not base.is_dir()):
            base.unlink()
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, topdown=False):
            for name in [*filenames, *dirnames]:
                path = Path(dirpath) / name
                rel = path.relative_to(git).as_posix()
                if path.is_symlink() or (path.is_file() and rel not in old):
                    path.unlink()
                elif path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
    for rel, (mode, content) in old.items():
        path = git / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = base64.b64decode(content)
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
        if path.stat().st_mode & 0o777 != mode:
            # Only the owner may chmod (often the host's user, not ours). The content
            # is what matters: a hook left without its exec bit doesn't run.
            with contextlib.suppress(PermissionError):
                os.chmod(path, mode)
    if "config" not in old and config.exists():
        config.unlink()


def nested_repos(root: str | Path) -> list[str]:
    """`.git` folders or files below the top level (each could hold hooks)."""
    top = Path(root)
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(top):
        here = Path(dirpath)
        if here == top:
            dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in [*dirnames, *filenames]:
            if name == ".git" and here != top:
                found.append((here / name).relative_to(top).as_posix())
                if len(found) >= MAX_NESTED:
                    return found
        dirnames[:] = [d for d in dirnames if d != ".git"]
    return found
