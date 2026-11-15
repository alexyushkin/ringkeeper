"""Browser (Playwright): one click on your own tools adds a browser your tasks can drive.

It's Microsoft's Playwright MCP server, pinned, installed into Ringkeeper's data folder like a
catalog server (services.local_servers), with a browser profile of its own there: never your
everyday one, so its logins are only the ones you make in it. It's a shared tool linked to no
project until you pick some. What it may do without asking is in ringkeeper.browser.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

from ringkeeper import browser
from ringkeeper.adapters import mcp
from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.models import Env, InstanceType
from ringkeeper.schemas import InstanceIn
from ringkeeper.services import admin_audit, instances, local_servers, secrets_in
from ringkeeper.services.remote_connections import NewConnection, check_new, link

PACKAGE = "@playwright/mcp"
VERSION = "0.0.84"  # 2026-10-31: pinned, two weeks old or more when chosen
NAME = "browser"
TITLE = "Browser (Playwright)"
PUBLISHER = "Microsoft"


def profile(settings: Settings) -> Path:
    """Its own browser profile: the logins you make in it stay here, apart from your own."""
    return settings.data_dir / "browser" / "profile"


def _chrome() -> bool:
    if sys.platform == "win32":
        roots = [
            os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
        ]
        return any(
            Path(r, "Google", "Chrome", "Application", "chrome.exe").is_file() for r in roots if r
        )
    if sys.platform == "darwin":
        return Path("/Applications/Google Chrome.app").exists()
    return any(local_servers.which(n) for n in ("google-chrome", "google-chrome-stable"))


def which_browser() -> str:
    """Chrome when it's here; on Windows, Edge otherwise (it always is)."""
    return "chrome" if _chrome() or sys.platform != "win32" else "msedge"


def plan() -> local_servers.Plan:
    summary: dict[str, Any] = {
        "name": TITLE, "publisher": PUBLISHER, "verified": "microsoft.com",
        "packages": [{"registry": "npm", "identifier": PACKAGE, "version": VERSION,
                      "usable": True}],
    }  # fmt: skip
    return local_servers.plan(summary)


def arguments(settings: Settings) -> list[str]:
    files = settings.data_dir / "browser" / "files"  # screenshots and files it saves
    return [
        "--browser", which_browser(), "--user-data-dir", str(profile(settings)),
        "--output-dir", str(files),
    ]  # fmt: skip


def start(settings: Settings, *, project: str | None = None) -> local_servers.Job:
    """Install it in the background (GET /connections/local/browser says how far it got).
    Linked to `project` only when one is named (a task's "Add a browser")."""
    new = NewConnection(
        name=NAME, owner=instances.SHARED_OWNER, env=Env.DEV, project=project, shared=True,
        title=TITLE,
    )  # fmt: skip
    with session_scope() as session:
        check_new(session, new)
    found = plan()

    def work() -> None:
        spec = local_servers.install(settings, found, {})
        spec["args"] = [*spec["args"], *arguments(settings)]
        profile(settings).mkdir(parents=True, exist_ok=True)
        _store(settings, new, spec)

    return local_servers.start(NAME, work)


def _store(settings: Settings, new: NewConnection, spec: dict[str, Any]) -> None:
    secret = {k: spec[k] for k in ("command", "args", "env")}
    config = {
        "source": browser.SOURCE, "registry": PACKAGE, "publisher": PUBLISHER,
        "package": {"registry": "npm", "identifier": PACKAGE, "version": VERSION,
                    "pinned": spec["pinned"]},
        "preview": mcp.preview(secret),
    }  # fmt: skip
    with session_scope() as session:
        ref = secrets_in.store_connection(
            session, settings, new.name, InstanceType.MCP, json.dumps(secret)
        )
        instances.create_instance(
            session,
            InstanceIn(
                name=new.name, title=new.title, type=InstanceType.MCP, env=new.env,
                secret_ref=ref, owner=new.owner, config=config, shared=True,
            ),
        )  # fmt: skip
        admin_audit.record(
            session, "connection.create",
            f"installed {PACKAGE} {VERSION} as {new.name}, with its own browser profile",
            by="owner", instance=new.name,
        )  # fmt: skip
        link(session, new)
