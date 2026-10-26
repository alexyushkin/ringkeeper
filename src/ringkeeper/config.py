import os
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ringkeeper.models import AuthMode


def _env_file() -> str | None:
    """`.env` by default; RINGKEEPER_ENV_FILE picks another, or "" for none (tests).

    Never in desktop mode: the app's settings live in its data folder, and a
    `.env` that happens to sit in the working folder (a Docker install's, say)
    must not point it at another database or workspaces folder.
    """
    if os.environ.get("RINGKEEPER_MODE") == "desktop":
        return None
    return os.environ.get("RINGKEEPER_ENV_FILE", ".env") or None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RINGKEEPER_", env_file=_env_file(), extra="ignore"
    )

    # server: the Docker install (Postgres, worker containers, owner sign-in).
    # desktop: the desktop app (SQLite in data_dir, the master key in the OS
    # keychain). Same core either way; see docs/DESKTOP_PLAN.md.
    mode: Literal["server", "desktop"] = "server"

    # Desktop mode defaults to data_dir/ringkeeper.db when this isn't set.
    database_url: str = "postgresql+psycopg://ringkeeper:ringkeeper@localhost:5432/ringkeeper"
    # Seconds; fail fast instead of hanging when the database is down.
    database_connect_timeout: int = 10

    # Holds vault.json and, unless RINGKEEPER_MASTER_KEY is set, master.key.
    # Only the gateway and the CLI may see this directory; workers never do.
    data_dir: Path = Path(".ringkeeper")
    # Base64 (32 bytes). Overrides data_dir/master.key (or, in desktop mode,
    # the keychain item) when set.
    master_key: SecretStr | None = None
    # Desktop mode: the OS keychain entry (Windows Credential Manager, macOS
    # Keychain) holding the master key. The secrets themselves stay in the vault file.
    keychain_service: str = "Ringkeeper"
    # Files the api (root) writes into data_dir are handed to this user, so the
    # gateway (which runs as it) can read them. Compose sets it; the CLI doesn't.
    data_uid: int | None = None

    token_max_ttl_seconds: int = 24 * 3600
    decision_ttl_seconds: int = 24 * 3600

    # How workers authenticate to Claude. api_key is the default and the only
    # mode offered in setup; subscription is for your own install on your own
    # account (Agent SDK terms) and must be switched on explicitly.
    claude_auth_mode: AuthMode = AuthMode.API_KEY
    claude_api_key_ref: str = "file://ringkeeper/anthropic-api-key"
    claude_oauth_token_ref: str = "file://ringkeeper/claude-oauth-token"  # noqa: S105 (a ref)
    # The Anthropic workspace an API key belongs to, for keys that must name one
    # (sent as the anthropic-workspace-id header wherever the key is used).
    anthropic_workspace_id: str | None = None
    # Bearer token for every API endpoint except /health (`ringkeeper auth api-token`).
    api_token_ref: str = "file://ringkeeper/api-token"  # noqa: S105 (a ref)
    # The web app's token: useless without a signed-in owner (see admin_token.py).
    web_token_ref: str = "file://ringkeeper/web-token"  # noqa: S105 (a ref)

    # Session manager (runs inside the api service).
    # How workers run: docker containers (server mode) or local processes
    # (desktop mode, the default there; see workers/process.py).
    worker_runtime: Literal["docker", "process"] = "docker"
    # Process workers: folders put first on their PATH (the bundled git, say).
    worker_tools_path: list[str] = []
    worker_image: str = "ringkeeper-worker:latest"
    # The network workers join. Only the gateway is on it, not Postgres or the api.
    worker_network: str = "ringkeeper_workers"
    gateway_internal_url: str = "http://gateway:8100"
    worker_memory: str = "2g"
    max_concurrent_workers: int = 4
    default_max_turns: int = 30
    default_max_runtime_seconds: int = 30 * 60
    # Relative workspace_path values resolve against this, as the Docker host sees it.
    workspaces_root: str | None = None
    manager_poll_seconds: float = 1.0
    # Run the session manager in this process (set for the api service only).
    session_manager: bool = False
    # Tests only: JSON options for the worker's stub agent (no LLM is called),
    # and extra /etc/hosts entries so a worker can reach a test gateway.
    worker_stub: str | None = None
    worker_extra_hosts: dict[str, str] = {}
    # Where workers' Claude Code sends model requests, if not Anthropic's API
    # directly (a company proxy; tests point it at a fake).
    worker_anthropic_base_url: str | None = None

    # Models by role, and by task complexity for workers the router dispatched.
    # A project's `models` in the registry overrides any of these for that project.
    model_router: str = "claude-haiku-5-5"
    model_architect: str = "claude-opus-5-5"
    model_worker: str = "claude-sonnet-5-5"  # tasks without a complexity (`task run`)
    model_low: str = "claude-haiku-5-5"
    model_medium: str = "claude-sonnet-5-5"
    model_high: str = "claude-opus-5-5"

    # Router (runs inside the api service, alongside the session manager).
    router_confidence_threshold: float = 0.8
    router_recent_messages: int = 10
    router_examples: int = 20
    router: bool = False

    # Telegram: a notification channel, not a secure one (bot chats aren't
    # end-to-end encrypted), so it only ever gets short scrubbed summaries.
    telegram_bot_token_ref: str = "file://ringkeeper/telegram-bot-token"  # noqa: S105 (a ref)
    telegram_chat_ids: list[int] = []
    telegram_summary_chars: int = 160  # approvals, plans, routes: one line
    telegram_question_chars: int = 1000  # questions must be readable to be answered
    telegram_poll_seconds: int = 25
    telegram_api_url: str = "https://api.telegram.org"
    telegram: bool = False
    # New requests from Telegram (typed or voice). Off by default: requests then
    # pass through Telegram's servers, which aren't end-to-end encrypted.
    telegram_requests: bool = False
    # How long an "I heard: ..." waits for Confirm or Redo.
    telegram_confirm_minutes: int = 10

    # Speech to text for voice notes (the optional `stt` compose service).
    # Empty turns voice off. Audio is never stored: only the transcript.
    stt_url: str = "http://stt:9000"
    stt_max_seconds: int = 180
    stt_max_bytes: int = 5_000_000
    stt_timeout_seconds: float = 120.0

    # Full session events (transcripts) older than this are deleted daily; task
    # summaries, decisions and the audit log are kept.
    retention_days: int = 90
    # "Stop everything": no new sessions start and the router waits.
    paused: bool = False
    # Desktop mode: the app shell starts Ringkeeper when the user signs in.
    start_at_login: bool = True

    # The web app (M5). The api writes the admin token here for the web service
    # (a volume only the two of them mount), and the setup code into data_dir.
    web_token_file: Path | None = None
    web_uid: int | None = None  # the web service's user id, which must be able to read it
    # Owner sessions: idle timeout and absolute lifetime, in hours.
    owner_session_idle_hours: int = 24 * 7
    owner_session_max_hours: int = 24 * 30
    # WebAuthn (passkeys): the web app's public origin, e.g. https://ringkeeper.tail1234.ts.net
    web_origin: str | None = None

    @field_validator("master_key", mode="before")
    @classmethod
    def _empty_key_is_unset(cls, value: object) -> object:
        # Compose passes RINGKEEPER_MASTER_KEY="" when it isn't set in .env.
        return None if value == "" else value

    @model_validator(mode="after")
    def _desktop_defaults(self) -> Self:
        if self.mode == "desktop":
            if "database_url" not in self.model_fields_set:
                self.database_url = f"sqlite:///{(self.data_dir / 'ringkeeper.db').as_posix()}"
            if "worker_runtime" not in self.model_fields_set:
                self.worker_runtime = "process"
        return self


def get_settings() -> Settings:
    # Not cached: tests and the CLI may point at a different database via env.
    return Settings(_env_file=_env_file())  # pyright: ignore[reportCallIssue]
