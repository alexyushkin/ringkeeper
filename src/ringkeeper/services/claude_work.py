"""What the Claude desktop app knows about your work, to offer as projects: the
folders picked in Cowork and the Code tab's sessions' folders, and the Claude
projects (Chat and Cowork) it keeps a copy of.

Desktop mode only: the files are on this computer, in Claude's own folder (next
to claude_desktop_config.json). Only paths, names, descriptions and a project's
instructions and files are read; never a conversation. Anything missing or not
in the expected form is skipped: these are the Claude app's files, not ours.

A Claude project becomes a Ringkeeper project with a new folder: its
instructions go in CLAUDE.md there (Claude Code, which runs the agents, reads
it), and its files (the project's knowledge) are copied into a folder of their
own. Its conversations and memory stay in Claude.
"""

import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Any, cast

from ringkeeper.config import Settings
from ringkeeper.services import workspaces
from ringkeeper.services.claude_desktop import config_paths

COWORK = "local-agent-mode-sessions"  # Cowork's sessions, and its copy of Claude projects
CODE_TAB = "claude-code-sessions"  # the Code tab's sessions
INSTRUCTIONS = "CLAUDE.md"
KNOWLEDGE = "Claude project files"
MAX_FILES = 200  # a project's knowledge, at most (each file at most MAX_FILE_BYTES)
MAX_FILE_BYTES = 30_000_000


def _claude_dirs() -> list[Path]:
    """The Claude desktop app's own folders that exist (its config sits in them)."""
    return [p.parent for p in config_paths() if p.parent.is_dir()]


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cast(dict[str, Any], data) if isinstance(data, dict) else None


def _sessions(base: Path) -> Iterator[tuple[Path, str]]:
    """Each session's file: Cowork's (one or two folders down) and the Code tab's."""
    cowork = base / COWORK
    for path in chain(cowork.glob("*/*/local_*.json"), cowork.glob("*/*/*/local_*.json")):
        yield path, "Cowork"
    for path in (base / CODE_TAB).glob("*/*/local_*.json"):
        yield path, "Claude Code"


def opened_folders() -> list[tuple[str, str]]:
    """(folder, where): the folders picked for Cowork sessions, and the folder each Code
    tab session started in (not the worktree it may have worked in). Repeats included."""
    found: list[tuple[str, str]] = []
    for base in _claude_dirs():
        for path, where in _sessions(base):
            data = _read(path)
            if data is None:
                continue
            if where == "Cowork":
                picked = data.get("userSelectedFolders")
                folders = cast(list[Any], picked) if isinstance(picked, list) else []
            else:
                folders = [data.get("originCwd") or data.get("cwd")]
            found += [(folder, where) for folder in folders if isinstance(folder, str) and folder]
    return found


@dataclass(frozen=True)
class ClaudeProject:
    id: str
    name: str
    description: str
    instructions: str
    folder: Path  # the Claude app's copy: metadata.json, docs/, files/

    def files(self) -> list[Path]:
        """Its knowledge files (documents and uploads), as the Claude app keeps them."""
        found = [
            path
            for part in ("docs", "files")
            for path in sorted((self.folder / part).glob("*"))
            if path.is_file() and path.stat().st_size <= MAX_FILE_BYTES
        ]
        return found[:MAX_FILES]


def claude_projects() -> list[ClaudeProject]:
    """The Claude projects the app keeps a copy of (the ones used in Cowork), each once."""
    found: dict[str, ClaudeProject] = {}
    for base in _claude_dirs():
        for meta in sorted((base / COWORK).glob("*/*/.project-cache/*/metadata.json")):
            data = _read(meta)
            if data is None or not isinstance(data.get("name"), str) or not data["name"].strip():
                continue
            project_id = str(data.get("uuid") or meta.parent.name)
            found.setdefault(
                project_id,
                ClaudeProject(
                    id=project_id,
                    name=data["name"].strip(),
                    description=str(data.get("description") or "").strip(),
                    instructions=str(data.get("prompt_template") or "").strip(),
                    folder=meta.parent,
                ),
            )
    return sorted(found.values(), key=lambda p: p.name.lower())


def find_project(project_id: str) -> ClaudeProject | None:
    return next((p for p in claude_projects() if p.id == project_id), None)


def bring_in(settings: Settings, project: ClaudeProject, folder: Path) -> tuple[bool, int]:
    """Its instructions as CLAUDE.md (unless the folder has one) and a copy of its files,
    committed. Returns (instructions written, files copied)."""
    wrote = False
    if project.instructions and not (folder / INSTRUCTIONS).exists():
        heading = f"# {project.name}\n\n" + (
            f"{project.description}\n\n" if project.description else ""
        )
        (folder / INSTRUCTIONS).write_text(f"{heading}{project.instructions}\n", encoding="utf-8")
        wrote = True
    copied = 0
    files = project.files()
    if files:
        target = folder / KNOWLEDGE
        target.mkdir(exist_ok=True)
        for path in files:
            if not (target / path.name).exists():
                shutil.copy2(path, target / path.name)
                copied += 1
    added = ([INSTRUCTIONS] if wrote else []) + ([KNOWLEDGE] if copied else [])
    workspaces.commit(settings, folder, added, f"Bring in the Claude project {project.name}")
    return wrote, copied
