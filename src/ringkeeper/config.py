from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ringkeeper.models import AuthMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RINGKEEPER_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://ringkeeper:ringkeeper@localhost:5432/ringkeeper"
    # Seconds; fail fast instead of hanging when the database is down.
    database_connect_timeout: int = 10

    # Holds vault.json and, unless RINGKEEPER_MASTER_KEY is set, master.key.
    # Only the gateway and the CLI may see this directory; workers never do.
    data_dir: Path = Path(".ringkeeper")
    # Base64 (32 bytes). Overrides data_dir/master.key when set.
    master_key: SecretStr | None = None

    token_max_ttl_seconds: int = 24 * 3600
    decision_ttl_seconds: int = 24 * 3600

    # How workers authenticate to Claude. api_key is the default and the only
    # mode offered in setup; subscription is for your own install on your own
    # account (Agent SDK terms) and must be switched on explicitly.
    claude_auth_mode: AuthMode = AuthMode.API_KEY
    claude_api_key_ref: str = "file://ringkeeper/anthropic-api-key"
    claude_oauth_token_ref: str = "file://ringkeeper/claude-oauth-token"  # noqa: S105 (a ref)

    # Session manager (runs inside the api service).
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

    @field_validator("master_key", mode="before")
    @classmethod
    def _empty_key_is_unset(cls, value: object) -> object:
        # Compose passes RINGKEEPER_MASTER_KEY="" when it isn't set in .env.
        return None if value == "" else value


def get_settings() -> Settings:
    # Not cached: tests and the CLI may point at a different database via env.
    return Settings()
