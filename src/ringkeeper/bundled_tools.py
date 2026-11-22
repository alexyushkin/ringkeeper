"""Node.js and uv that come with the desktop app, for the tools that run with npx or uvx.

shell/scripts/bundle.py puts them in the install folder beside the bundled Python:
tools/node (node, npm, npx) and tools/uv (uv, uvx). When the desktop app starts, their folders
go first on its PATH, so every check for a program ("needs Node.js (npx)") and every server it
starts finds them, with nothing to install. A developer's copy, run from its own environment,
has none and uses what's on the computer.
"""

import os
import sys
from pathlib import Path


def folders(prefix: str | None = None) -> list[Path]:
    """The bundled tools' folders that are there: `prefix` is the bundled Python's."""
    root = Path(prefix or sys.prefix).parent / "tools"
    node = root / "node" if sys.platform == "win32" else root / "node" / "bin"
    return [folder for folder in (node, root / "uv") if folder.is_dir()]


def add_to_path(prefix: str | None = None) -> list[Path]:
    """Put the bundled tools' folders first on this process's PATH (and so on the PATH of
    everything it starts). Returns the folders added."""
    found = folders(prefix)
    if not found:
        return []
    already = os.environ.get("PATH", "")
    known = {os.path.normcase(p) for p in already.split(os.pathsep) if p}
    added = [f for f in found if os.path.normcase(str(f)) not in known]
    if added:
        os.environ["PATH"] = os.pathsep.join([*(str(f) for f in added), already])
    return added
