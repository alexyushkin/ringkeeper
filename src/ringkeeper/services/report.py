"""Report a problem: what Ringkeeper sends with a bug report, shown to the user first.

The report is plain text: the version, the operating system, how it's installed,
a few counts (never names of clients, projects or connections), and the end of
the desktop app's logs. Every secret Ringkeeper knows is removed (the scrubber),
and so is anything shaped like a key, a token, a password in an address, or an
email address. The user reads it, can edit it, and then opens a prefilled GitHub
issue or saves it as a zip; nothing is sent by Ringkeeper itself.
"""

import io
import platform
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ringkeeper import __version__
from ringkeeper.config import Settings
from ringkeeper.models import Instance, Project, Routine, Task
from ringkeeper.scrub import scrub

LOG_LINES = 200
LOGS = ("backend.log", "shell.log")
REDACTED = "[removed]"

# Shapes of secrets, whoever's they are: the scrubber only knows Ringkeeper's own.
SHAPES = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{8,}"),
    re.compile(r"\bxapp-[A-Za-z0-9-]{8,}"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"\bpat[A-Za-z0-9]{14}\.[A-Za-z0-9]{20,}"),  # Airtable
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bntn_[A-Za-z0-9]{20,}"),  # Notion
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),  # JWTs
    re.compile(
        r"(?i)\b(bearer|token|api[_-]?key|password|secret)(\s*[:=]\s*|\s+)['\"]?[^\s'\",;]{6,}"
    ),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),  # Telegram bot tokens
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),  # email addresses
]
# user:password@ in an address keeps the user and host, not the password.
CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^:/@\s]+):[^@\s/]+@")


def clean(text: str) -> str:
    """Ringkeeper's own secrets, then anything shaped like a secret, removed."""
    text = scrub(text)
    text = CREDENTIALS.sub(lambda m: f"{m['scheme']}{m['user']}:{REDACTED}@", text)
    for shape in SHAPES:
        text = shape.sub(REDACTED, text)
    return text


def _tail(path: Path, lines: int = LOG_LINES) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readlines()[-lines:])
    except OSError:
        return ""


def collect(session: Session, settings: Settings) -> dict[str, Any]:
    """The report's parts, each already cleaned."""

    def count(model: Any) -> int:
        return session.scalar(select(func.count()).select_from(model)) or 0

    failed = session.scalar(select(func.count()).select_from(Task).where(Task.status == "failed"))
    about = {
        "version": __version__,
        "install": "Desktop app (Windows)" if settings.mode == "desktop" else "Server (Docker)",
        "system": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": platform.python_version(),
        "claude": settings.claude_auth_mode.value,
        "counts": f"{count(Project)} projects, {count(Instance)} connections, "
        f"{count(Routine)} routines, {count(Task)} tasks ({failed or 0} failed)",
    }
    logs: dict[str, str] = {}
    if settings.mode == "desktop":
        for name in LOGS:
            text = _tail(settings.data_dir / "logs" / name)
            if text:
                logs[name] = clean(text)
    return {"about": about, "logs": logs}


def as_text(parts: dict[str, Any]) -> str:
    about = parts["about"]
    lines = [f"{key}: {value}" for key, value in about.items()]
    for name, text in parts["logs"].items():
        lines += ["", f"--- last lines of {name} ---", text.rstrip()]
    return "\n".join(lines) + "\n"


def zipped(text: str, what: str) -> bytes:
    """The report as a zip, for attaching by hand."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("what-happened.txt", clean(what) + "\n")
        archive.writestr("report.txt", clean(text))
    return out.getvalue()


def file_name(now: datetime | None = None) -> str:
    return f"Ringkeeper-report-{(now or datetime.now(UTC)):%Y-%m-%d-%H%M}.zip"
