from alembic import context

from ringkeeper.config import get_settings
from ringkeeper.models import Base
from ringkeeper.storage.sql import is_sqlite, make_engine

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    # Callers (tests, `ringkeeper db upgrade`) may pass an explicit URL.
    url = config.attributes.get("database_url")
    return url if isinstance(url, str) else get_settings().database_url


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True, render_as_batch=is_sqlite(url)
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Migrations from 0009 on run on both databases. SQLite can't ALTER most
    # things in place, so there alembic rebuilds tables ("batch" mode).
    url = _database_url()
    engine = make_engine(url, pooled=False)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=is_sqlite(url)
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
