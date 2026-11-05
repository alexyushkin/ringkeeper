"""Logins on this computer, offered as your own connections.

The command-line tools you're signed in to: GitHub CLI, Vercel CLI, AWS profiles and SSO,
Azure CLI, Google Cloud CLI and OCI. Each is offered once and connected only when you
confirm that one, as your own tool: you then choose which clients' projects use it. What's
shown is who the login is, never a token. Desktop mode only: these files are on this
computer.

GitHub and Vercel connect now, with the token read at that moment. The cloud logins are
listed but can't connect yet: Ringkeeper has no connection for those clouds.
"""

import configparser
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from ringkeeper.errors import RuleViolationError

NO_CLOUD_YET = "Ringkeeper has no connection for this cloud yet, so it stays as it is."
GH_TIMEOUT = 15
GITHUB_COM = "github.com"


@dataclass(frozen=True)
class Login:
    id: str
    tool: str  # as people know it: "GitHub CLI"
    account: str  # who it signs in as: never a secret
    service: str  # the connection type it would become, or a cloud's name
    title: str
    usable: bool = True
    why: str = ""
    host: str = ""  # GitHub Enterprise Server's address


def _id(tool: str, account: str) -> str:
    return hashlib.sha256(f"{tool}|{account}".encode()).hexdigest()[:16]


def _login(tool: str, account: str, service: str, title: str, **fields: Any) -> Login:
    return Login(_id(tool, account), tool, account, service, title, **fields)


def _appdata() -> Path | None:
    value = os.environ.get("APPDATA")
    return Path(value) if value else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return cast(dict[str, Any], data) if isinstance(data, dict) else {}


def _sections(path: Path) -> dict[str, dict[str, str]]:
    """An ini file's sections and values; nothing if it can't be read."""
    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error, UnicodeDecodeError):
        return {}
    return {name: dict(parser.items(name)) for name in parser.sections()} | (
        {"DEFAULT": dict(parser.defaults())} if parser.defaults() else {}
    )


# --- GitHub CLI ---


def _gh_hosts() -> dict[str, Any]:
    folders = [Path.home() / ".config" / "gh"]
    if env := os.environ.get("GH_CONFIG_DIR"):
        folders.insert(0, Path(env))
    if appdata := _appdata():
        folders.append(appdata / "GitHub CLI")
    for folder in folders:
        try:
            data: Any = yaml.safe_load((folder / "hosts.yml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            return cast(dict[str, Any], data)
    return {}


def _github() -> Iterator[Login]:
    for host, entry in _gh_hosts().items():
        fields = cast(dict[str, Any], entry) if isinstance(entry, dict) else {}
        user = str(fields.get("user") or "someone")
        account = user if host == GITHUB_COM else f"{user} on {host}"
        yield _login(
            "GitHub CLI", account, "github", f"GitHub · {account}",
            host="" if host == GITHUB_COM else f"https://{host}",
        )  # fmt: skip


def _gh_token(login: Login) -> str:
    host = login.host.removeprefix("https://") or GITHUB_COM
    stored = cast(dict[str, Any], _gh_hosts().get(host) or {}).get("oauth_token")
    if stored:
        return str(stored)
    command = shutil.which("gh")
    if command is None:
        raise RuleViolationError("The GitHub CLI isn't on this computer any more.")
    try:
        done = subprocess.run(  # noqa: S603 (fixed arguments)
            [command, "auth", "token", "--hostname", host],
            capture_output=True, text=True, timeout=GH_TIMEOUT, check=True,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        raise RuleViolationError(
            "The GitHub CLI didn't give a token. Run gh auth login, then try again."
        ) from None
    return done.stdout.strip()


# --- Vercel CLI ---


def _vercel_file() -> Path | None:
    places = [Path.home() / ".local" / "share" / "com.vercel.cli" / "auth.json"]
    if sys.platform == "darwin":
        support = Path.home() / "Library" / "Application Support"
        places.insert(0, support / "com.vercel.cli" / "auth.json")
    if appdata := _appdata():
        places.insert(0, appdata / "com.vercel.cli" / "Data" / "auth.json")
    return next((p for p in places if p.is_file()), None)


def _vercel() -> Iterator[Login]:
    path = _vercel_file()
    data = _read_json(path) if path else {}
    if not data.get("token"):
        return
    ends = data.get("expiresAt")
    expired = isinstance(ends, int | float) and ends < time.time()
    yield _login(
        "Vercel CLI", "your Vercel login", "vercel", "Vercel · your login",
        usable=not expired, why="This login has ended. Run vercel login first." if expired else "",
    )  # fmt: skip


def _vercel_token() -> str:
    path = _vercel_file()
    token = _read_json(path).get("token") if path else None
    if not token:
        raise RuleViolationError("The Vercel CLI isn't signed in any more. Run vercel login.")
    return str(token)


# --- The clouds: listed, not connectable yet ---


def _aws() -> Iterator[Login]:
    folder = Path.home() / ".aws"
    seen: set[str] = set()
    for name, values in _sections(folder / "config").items():
        profile = name.removeprefix("profile ").strip()
        if name.startswith("sso-session") or name == "DEFAULT" or profile in seen:
            continue
        seen.add(profile)
        sso = "sso_start_url" in values or "sso_session" in values
        kind = "SSO" if sso else "keys"
        where = values.get("region", "no region set")
        yield _login("AWS", f"{profile} ({kind}, {where})", "aws", f"AWS · {profile}",
                     usable=False, why=NO_CLOUD_YET)  # fmt: skip
    for profile in _sections(folder / "credentials"):
        if profile not in seen and profile != "DEFAULT":
            seen.add(profile)
            yield _login("AWS", f"{profile} (keys)", "aws", f"AWS · {profile}",
                         usable=False, why=NO_CLOUD_YET)  # fmt: skip


def _azure() -> Iterator[Login]:
    profile = _read_json(Path.home() / ".azure" / "azureProfile.json")
    for entry in cast(list[Any], profile.get("subscriptions") or []):
        if isinstance(entry, dict):
            fields = cast(dict[str, Any], entry)
            name = str(fields.get("name") or fields.get("id") or "a subscription")
            yield _login("Azure CLI", name, "azure", f"Azure · {name}",
                         usable=False, why=NO_CLOUD_YET)  # fmt: skip


def _gcloud_folder() -> Path:
    if appdata := _appdata():
        found = appdata / "gcloud"
        if found.is_dir():
            return found
    return Path.home() / ".config" / "gcloud"


def _gcloud() -> Iterator[Login]:
    folder = _gcloud_folder() / "configurations"
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("config_*")):
        core = _sections(path).get("core") or {}
        if account := core.get("account"):
            project = core.get("project", "no project set")
            yield _login("Google Cloud CLI", f"{account} ({project})", "gcloud",
                         f"Google Cloud · {project}", usable=False, why=NO_CLOUD_YET)  # fmt: skip


def _oci() -> Iterator[Login]:
    for name, values in _sections(Path.home() / ".oci" / "config").items():
        if values.get("tenancy"):
            region = values.get("region", "no region set")
            yield _login("Oracle Cloud (OCI)", f"{name} ({region})", "oci", f"OCI · {name}",
                         usable=False, why=NO_CLOUD_YET)  # fmt: skip


def find() -> list[Login]:
    """Everything signed in on this computer. Reads its own files; opens no connection."""
    return [*_github(), *_vercel(), *_aws(), *_azure(), *_gcloud(), *_oci()]


def token_of(login: Login) -> str:
    """The login's token, read now, for the vault. Never shown or logged."""
    return _gh_token(login) if login.service == "github" else _vercel_token()
