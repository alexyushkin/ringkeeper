"""Keeps the api's background parts in line with the effective settings.

Runs in the api service. On start, and whenever settings change (NOTIFY, or
every 30 seconds), it:

- starts, restarts or stops the Telegram bridge when its settings change
  (turned on in the Settings screen, a new chat id, a new bot token);
- restarts the router when its model changes;
- writes the web token to the web service's token file, so a new one
  (`ringkeeper web reset-login`) reaches the web app.

The session manager doesn't need it: it reads the effective settings on
every tick.
"""

import logging
import os
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ringkeeper.admin_token import stored_web_token
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.listen import start_listener
from ringkeeper.services import backup, digest, health, retention, undo
from ringkeeper.services.runtime import SETTINGS_CHANNEL, current_settings
from ringkeeper.vault import get_vault

logger = logging.getLogger(__name__)

RECHECK_SECONDS = 30.0
KINDS = ("slack:", "discord:")  # Slack and Discord channels' runners


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
        settings.telegram_confirm_minutes, settings.stt_url, settings.stt_language,
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
        self._backup(settings)
        self._daily(settings)
        if settings.router:
            # The router creates the tasks, so the model map and auth mode travel with it.
            key = (
                settings.model_router, settings.model_architect, settings.model_worker,
                settings.model_low, settings.model_medium, settings.model_high,
                settings.claude_auth_mode, settings.anthropic_workspace_id,
            )  # fmt: skip
            self._ensure("router", key, lambda: self._router(settings))
        # Routines run while the app runs; each checks its own pauses (services.routines).
        self._ensure("routines", ("on",), lambda: self._scheduler(settings))
        self._channels(settings)
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

    def _scheduler(self, settings: Settings) -> Callable[..., None]:
        from ringkeeper.scheduler import Scheduler  # noqa: PLC0415

        return Scheduler(settings).run_forever

    def _channels(self, settings: Settings) -> None:
        """A runner per Slack or Discord channel that's on; any change restarts it."""
        from ringkeeper.channels import list_channels  # noqa: PLC0415

        with session_scope(commit=False) as session:
            wanted = {
                f"{c.kind}:{c.id}": (c.id, c.updated_at.isoformat())
                for c in list_channels(session)
                if c.enabled
            }
        for name, (ident, changed) in wanted.items():
            self._ensure(name, (changed,), lambda ident=ident: self._channel(settings, ident))
        with self._lock:
            gone = [n for n in self.children if n.startswith(KINDS) and n not in wanted]
        for name in gone:
            self._halt(name)
            with self._lock:
                self.children.pop(name, None)

    @staticmethod
    def _channel(settings: Settings, ident: uuid.UUID) -> Callable[..., None]:
        from ringkeeper.channels import get_channel, transport  # noqa: PLC0415
        from ringkeeper.channels.outbox import Runner  # noqa: PLC0415

        with session_scope(commit=False) as session:
            channel = get_channel(session, ident)
            return Runner(ident, settings, transport(settings, channel)).run_forever

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
                expired = undo.expire()  # kept changes past their 7 days
                if expired:
                    logger.info("undo: deleted %d kept changes older than 7 days", expired)
                if deleted:
                    logger.info(
                        "retention: deleted %d transcript lines older than %d days",
                        deleted, settings.retention_days,
                    )  # fmt: skip

    @staticmethod
    def _backup(settings: Settings) -> None:
        """The weekly automatic backup (desktop), when it's on and due."""
        if settings.mode != "desktop":
            return
        try:
            with session_scope() as session:
                written = backup.run_auto(session, settings, get_vault())
        except Exception:
            logger.exception("the weekly backup failed")
            return
        if written is not None:
            logger.info("weekly backup written: %s", written)

    @staticmethod
    def _daily(settings: Settings) -> None:
        """The connection check and the morning digest, when they're due."""
        try:
            health.run_if_due(settings)
            with session_scope() as session:
                digest.send_if_due(session, settings)
        except Exception:
            logger.exception("a daily job failed")

    # --- The web app's token ---

    @staticmethod
    def _sync_web_token(settings: Settings) -> None:
        path = settings.web_token_file
        if path is None:
            return
        token = stored_web_token()
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
