"""Keys in a project's own files, offered as connections.

When a project is added (or on demand) its folder's .env files and config files are
read for known keys: DATABASE_URL, AIRTABLE_*, N8N_*, VERCEL_TOKEN, TWILIO_*,
GITHUB_TOKEN and the like. Each is offered as a connection for that client, masked,
and connected only when you confirm that one. Nothing is imported by itself.

Only the named files in the project's folder and its config/ folder are read, never
through a link that leaves the folder. A value never leaves the server: what you see
is masked, and confirming names a finding, whose value is read again from the file.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MAX_BYTES = 256_000
ENV_FILES = (".env", ".env.local", ".env.development", ".env.development.local",
             ".env.production", ".env.production.local", ".env.staging")  # fmt: skip
CONFIG_FILES = ("config.json", "config.local.json", "settings.json", "secrets.json",
                "config.yaml", "config.yml", "config.toml")  # fmt: skip
_LINE = re.compile(
    r"""^\s*(?:export\s+)?["']?([A-Za-z][A-Za-z0-9_]*)["']?\s*[=:]\s*(.*?)\s*,?\s*$"""
)
PLACEHOLDER = re.compile(r"^(<.*>|\$\{.*\}|your[-_ ].*|xxx+|changeme|todo|example.*)$", re.I)


@dataclass(frozen=True)
class Finding:
    id: str
    file: str  # relative to the project's folder
    service: str  # a connection type
    label: str
    keys: list[str]
    masked: str
    title: str
    usable: bool = True
    why: str = ""  # why it can't be connected as it is


def mask(value: str) -> str:
    """Enough to recognize it, nothing to use: a database's host, a key's ends."""
    if "://" in value:
        host = urlsplit(value).hostname or "?"
        return f"{value.split('://', 1)[0]}://…@{host}"
    return f"{value[:4]}…{value[-2:]}" if len(value) > 10 else "…"


def _inside(folder: Path, path: Path) -> bool:
    try:
        return path.is_file() and path.resolve().is_relative_to(folder.resolve())
    except OSError:
        return False


def files_in(folder: Path) -> list[Path]:
    names = [folder / n for n in (*ENV_FILES, *CONFIG_FILES)]
    names += [folder / "config" / n for n in (*ENV_FILES, *CONFIG_FILES)]
    return [p for p in names if _inside(folder, p) and p.stat().st_size <= MAX_BYTES]


def _flatten(data: object, found: dict[str, str]) -> None:
    if isinstance(data, dict):
        for key, value in data.items():  # pyright: ignore[reportUnknownVariableType]
            if isinstance(value, str):
                found.setdefault(str(key), value)  # pyright: ignore[reportUnknownArgumentType]
            else:
                _flatten(value, found)  # pyright: ignore[reportUnknownArgumentType]


def values_in(path: Path) -> dict[str, str]:
    """KEY -> value, from an env file or a config file's plain keys."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    found: dict[str, str] = {}
    if path.suffix == ".json":
        try:
            _flatten(json.loads(text), found)
        except ValueError:
            return {}
    else:
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            match = _LINE.match(line)
            if match:
                found.setdefault(match[1], match[2].strip("\"'"))
    found = {k.upper(): v for k, v in found.items()}
    return {k: v for k, v in found.items() if v and not PLACEHOLDER.match(v)}


def _id(file: str, service: str, keys: list[str]) -> str:
    return hashlib.sha256(f"{file}|{service}|{','.join(keys)}".encode()).hexdigest()[:16]


def _first(values: dict[str, str], *names: str) -> tuple[str, str] | None:
    return next(((n, values[n]) for n in names if n in values), None)


def _in_file(file: str, values: dict[str, str]) -> list[tuple[Finding, str, str | None]]:
    """What this file's keys can connect: (finding, secret, address)."""
    found: list[tuple[Finding, str, str | None]] = []

    def add(
        service: str, label: str, keys: list[str], secret: str, *, title: str,
        address: str | None = None, shown: str | None = None, why: str = "",
    ) -> None:  # fmt: skip
        finding = Finding(
            _id(file, service, keys), file, service, label, keys, shown or mask(secret), title,
            usable=not why, why=why,
        )  # fmt: skip
        found.append((finding, secret, address))

    database = _first(
        values, "DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL", "SUPABASE_DB_URL",
        "SUPABASE_DATABASE_URL", "NEON_DATABASE_URL",
    )  # fmt: skip
    if database and database[1].startswith(("postgres://", "postgresql://")):
        host = urlsplit(database[1]).hostname or "database"
        add("supabase", "Database", [database[0]], database[1], title=f"Database · {host}")
    elif "SUPABASE_URL" in values:
        keys = [k for k in values if k.startswith("SUPABASE_")]
        add(
            "supabase", "Supabase", keys, values["SUPABASE_URL"], title="Database",
            why="Its API keys don't reach the database. Add its connection string, or sign in "
            "with the Supabase tile.",
        )  # fmt: skip
    airtable = _first(values, "AIRTABLE_TOKEN", "AIRTABLE_PAT", "AIRTABLE_API_KEY", "AIRTABLE_KEY")
    if airtable:
        add("airtable", "Airtable", [airtable[0]], airtable[1], title="Airtable")
    n8n_key = _first(values, "N8N_API_KEY", "N8N_KEY")
    n8n_url = _first(values, "N8N_BASE_URL", "N8N_URL", "N8N_HOST", "N8N_API_URL")
    if n8n_key:
        host = urlsplit(n8n_url[1]).hostname if n8n_url and "://" in n8n_url[1] else None
        keys = [n8n_key[0], *([n8n_url[0]] if n8n_url else [])]
        add(
            "n8n", "n8n", keys, n8n_key[1], title=f"n8n · {host}" if host else "n8n",
            address=n8n_url[1] if n8n_url else None,
            why="" if n8n_url else "It needs the n8n address too (N8N_BASE_URL).",
        )  # fmt: skip
    vercel = _first(values, "VERCEL_TOKEN", "VERCEL_API_TOKEN")
    if vercel:
        add("vercel", "Vercel", [vercel[0]], vercel[1], title="Vercel")
    sid = values.get("TWILIO_ACCOUNT_SID")
    if sid:
        key = _first(values, "TWILIO_API_KEY", "TWILIO_API_KEY_SID", "TWILIO_KEY_SID")
        secret = _first(values, "TWILIO_API_SECRET", "TWILIO_API_KEY_SECRET", "TWILIO_KEY_SECRET")
        keys = ["TWILIO_ACCOUNT_SID", *([key[0]] if key else []), *([secret[0]] if secret else [])]
        pair = json.dumps({"account_sid": sid, "key_sid": key[1] if key else "",
                           "key_secret": secret[1] if secret else ""})  # fmt: skip
        add(
            "twilio", "Twilio", keys, pair, title="Twilio", shown=mask(sid),
            why="" if key and secret else "It needs an API key and its secret, not the auth token.",
        )  # fmt: skip
    github = _first(values, "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT")
    if github:
        add("github", "GitHub", [github[0]], github[1], title="GitHub")
    return found


def scan(folder: Path) -> list[tuple[Finding, str, str | None]]:
    """Every finding in the project's files, with what it would store. The same key
    in two files shows once, from the first."""
    found: list[tuple[Finding, str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    for path in files_in(folder):
        file = path.relative_to(folder).as_posix()
        for finding, secret, address in _in_file(file, values_in(path)):
            mark = (finding.service, secret)
            if mark not in seen:
                seen.add(mark)
                found.append((finding, secret, address))
    return found
