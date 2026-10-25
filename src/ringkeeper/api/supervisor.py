"""Keeps the api's background parts in line with the effective settings.

Runs in the api service. On start, and whenever settings change (NOTIFY, or
every 30 seconds), it:

- starts, restarts or stops the Telegram bridge when its settings change
  (turned on in the Settings screen, a new chat id, a new bot token);
- restarts the router when its model changes;
- writes the admin token to the web service's token file, so a rotation
  (`ringkeeper auth api-token --rotate`) reaches the web app.

The session manager doesn't need it: it reads the effective settings on
every tick.
"""

import logging
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ringkeeper.admin_token import stored_api_token
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.listen import start_listener
from ringkeeper.services import retention
from ringkeeper.services.runtime import SETTINGS_CHANNEL, current_settings
from ringkeeper.vault import get_vault

logger = logging.getLogger(__name__)

RECHECK_SECONDS = 30.0


@dataclass
class Child:
    """A background loop the supervisor can restart: `run(stop)` until stop is set."""

    name: str
    key: tuple[object, ...] = ()
    stop: threading.Event | None = None
    error: str | None = None


def telegram_key(settings: Settings, token: str | None) -> tuple[object, ...]:
    return (
        settings.telegram, token, tuple(settings.telegram_chat_ids), settings.telegram_requests,
        settings.telegram_summary_chars, settings.telegram_question_chars,
        settings.telegram_confirm_minutes, settings.stt_url,
    )  # fmt: skip


@dataclass
class Supervisor:
    base: Settings = field(default_factory=get_settings)
    children: dict[str, Child] = field(default_factory=dict[str, Child])
    _wake: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def run_forever(self, stop: threading.Event) -> None:
        start_listener(
            self.base, [SETTINGS_CHANNEL], lambda _c, _p: self._wake.set(), stop, "rk-settings"
        )
        while not stop.is_set():
            try:
                self.reconcile()
            except Exception:
                logger.exception("supervisor pass failed")
            self._wake.wait(RECHECK_SECONDS)
            self._wake.clear()
        for child in self.children.values():
            if child.stop is not None:
                child.stop.set()

    def wake(self) -> None:
        self._wake.set()

    def status(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {
                name: {"running": child.stop is not None, "error": child.error}
                for name, child in self.children.items()
            }

    def reconcile(self) -> None:
        settings = current_settings(self.base)
        self._sync_web_token(settings)
        self._retention(settings)
        if settings.router:
            # The router creates the tasks, so the model map and auth mode travel with it.
            key = (
                settings.model_router, settings.model_architect, settings.model_worker,
                settings.model_low, settings.model_medium, settings.model_high,
                settings.claude_auth_mode,
            )  # fmt: skip
            self._ensure("router", key, lambda: self._router(settings))
        token = self._telegram_token(settings) if settings.telegram else None
        if token is not None:
            self._ensure("telegram", telegram_key(settings, token), lambda: self._bridge(settings))
        else:
            self._halt("telegram")
            if settings.telegram:
                with self._lock:
                    self.children["telegram"].error = "no bot token is stored yet"

    # --- Children ---

    def _ensure(
        self, name: str, key: tuple[object, ...], make: Callable[[], Callable[..., None]]
    ) -> None:
        with self._lock:
            child = self.children.setdefault(name, Child(name))
            if child.stop is not None and child.key == key:
                return
            if child.stop is not None:
                child.stop.set()
                logger.info("restarting %s: its settings changed", name)
            child.key, child.stop, child.error = key, None, None
            try:
                run = make()
            except RingkeeperError as error:
                child.error = str(error)
                logger.warning("%s not started: %s", name, error)
                return
            child.stop = threading.Event()
            threading.Thread(target=run, args=(child.stop,), daemon=True, name=f"rk-{name}").start()

    def _halt(self, name: str) -> None:
        with self._lock:
            child = self.children.setdefault(name, Child(name))
            child.error = None
            if child.stop is not None:
                child.stop.set()
                child.stop, child.key = None, ()
                logger.info("stopped %s: turned off in settings", name)

    def _router(self, settings: Settings) -> Callable[..., None]:
        from ringkeeper.router.model import AgentSdkModel  # noqa: PLC0415 (Agent SDK: only here)
        from ringkeeper.router.service import Router  # noqa: PLC0415

        return Router(AgentSdkModel(get_vault(settings), settings), settings).run_forever

    def _bridge(self, settings: Settings) -> Callable[..., None]:
        from ringkeeper.telegram.api import from_vault  # noqa: PLC0415
        from ringkeeper.telegram.bridge import Bridge  # noqa: PLC0415

        return Bridge(from_vault(get_vault(settings), settings), settings).run_forever

    @staticmethod
    def _telegram_token(settings: Settings) -> str | None:
        try:
            return get_vault(settings).resolve(settings.telegram_bot_token_ref).reveal()
        except RingkeeperError:
            return None

    # --- Daily retention ---

    @staticmethod
    def _retention(settings: Settings) -> None:
        with session_scope() as session:
            if retention.due(session):
                deleted = retention.purge(session, settings.retention_days)
                if deleted:
                    logger.info(
                        "retention: deleted %d transcript lines older than %d days",
                        deleted, settings.retention_days,
                    )  # fmt: skip

    # --- The web app's copy of the admin token ---

    @staticmethod
    def _sync_web_token(settings: Settings) -> None:
        path = settings.web_token_file
        if path is None:
            return
        token = stored_api_token()
        if token is None:
            return
        write_private(path, token, owner_uid=settings.web_uid)


def write_private(path: Path, value: str, owner_uid: int | None = None) -> None:
    """Write a small secret file readable by its owner only, atomically, if it changed.

    `owner_uid` hands the file to another user (the web service's), which only
    works when this process runs as root, as the api does.
    """
    try:
        if path.exists() and path.read_text(encoding="utf-8").strip() == value:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value + "\n")
    if owner_uid is not None and sys.platform != "win32":
        os.chown(tmp, owner_uid, -1)
    os.replace(tmp, path)
