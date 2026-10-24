from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INTERLOCK_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://interlock:interlock@localhost:5432/interlock"
    # Seconds; fail fast instead of hanging when the database is down.
    database_connect_timeout: int = 10

    # Holds vault.json and, unless INTERLOCK_MASTER_KEY is set, master.key.
    # Only the gateway and the CLI may see this directory; workers never do.
    data_dir: Path = Path(".interlock")
    # Base64 (32 bytes). Overrides data_dir/master.key when set.
    master_key: SecretStr | None = None

    token_max_ttl_seconds: int = 24 * 3600
    decision_ttl_seconds: int = 24 * 3600


def get_settings() -> Settings:
    # Not cached: tests and the CLI may point at a different database via env.
    return Settings()
