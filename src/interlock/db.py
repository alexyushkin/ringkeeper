from collections.abc import Generator
from contextlib import contextmanager
from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from interlock.config import get_settings


@cache
def _engine_for(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def get_engine() -> Engine:
    return _engine_for(get_settings().database_url)


@contextmanager
def session_scope() -> Generator[Session]:
    """One unit of work: commit on success, roll back on any error."""
    session = Session(get_engine(), expire_on_commit=False)
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
