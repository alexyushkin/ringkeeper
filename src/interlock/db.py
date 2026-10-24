from collections.abc import Generator
from contextlib import contextmanager
from functools import cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from interlock.config import get_settings


@cache
def _engine_for(url: str, connect_timeout: int) -> Engine:
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": connect_timeout})


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
