import os
from dataclasses import dataclass


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class SttConfig:
    model: str = "small"
    language: str | None = None  # None: detect it per recording
    max_seconds: float = 180.0
    max_bytes: int = 5_000_000
    port: int = 9000
    threads: int = 0  # 0: let CTranslate2 pick


def _number[T: (int, float)](env: dict[str, str], name: str, kind: type[T], default: T) -> T:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = kind(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number") from None
    if value < 0:
        raise ConfigError(f"{name} must not be negative")
    return value


def load(env: dict[str, str] | None = None) -> SttConfig:
    env = dict(os.environ) if env is None else env
    defaults = SttConfig()
    return SttConfig(
        model=env.get("RINGKEEPER_STT_MODEL", "").strip() or defaults.model,
        language=env.get("RINGKEEPER_STT_LANGUAGE", "").strip() or None,
        max_seconds=_number(env, "RINGKEEPER_STT_MAX_SECONDS", float, defaults.max_seconds),
        max_bytes=_number(env, "RINGKEEPER_STT_MAX_BYTES", int, defaults.max_bytes),
        port=_number(env, "RINGKEEPER_STT_PORT", int, defaults.port),
        threads=_number(env, "RINGKEEPER_STT_THREADS", int, defaults.threads),
    )
