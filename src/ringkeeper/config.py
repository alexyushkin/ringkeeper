from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @field_validator("master_key", mode="before")
    @classmethod
    def _empty_key_is_unset(cls, value: object) -> object:
        # Compose passes RINGKEEPER_MASTER_KEY="" when it isn't set in .env.
        return None if value == "" else value


def get_settings() -> Settings:
    # Not cached: tests and the CLI may point at a different database via env.
    return Settings()
