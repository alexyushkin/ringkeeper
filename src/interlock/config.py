from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INTERLOCK_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://interlock:interlock@localhost:5432/interlock"
    # Seconds; fail fast instead of hanging when the database is down.
    database_connect_timeout: int = 10


def get_settings() -> Settings:
    # Not cached: tests and the CLI may point at a different database via env.
    return Settings()
