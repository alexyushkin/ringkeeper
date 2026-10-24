from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from ringkeeper.config import get_settings

_engines: dict[tuple[str, int], Engine] = {}


def _engine_for(url: str, connect_timeout: int) -> Engine:
    key = (url, connect_timeout)
    if key not in _engines:
        _engines[key] = create_engine(
            url, pool_pre_ping=True, connect_args={"connect_timeout": connect_timeout}
        )
    return _engines[key]


def dispose_engines() -> None:
    """Close every pooled connection (e.g. after a schema change under them)."""
    for engine in _engines.values():
        engine.dispose()


def get_engine() -> Engine:
    settings = get_settings()
    return _engine_for(settings.database_url, settings.database_connect_timeout)


@contextmanager
def session_scope(*, commit: bool = True) -> Generator[Session]:
    """One unit of work: commit on success, roll back on any error.

    With commit=False everything is rolled back, which is how dry runs see
    the exact same rule checks as real runs without keeping any writes.
    """
    session = Session(get_engine(), expire_on_commit=False)
    try:
        yield session
        if commit:
            session.commit()
        else:
            session.rollback()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
