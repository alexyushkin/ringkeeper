from alembic import context
from sqlalchemy import create_engine, pool

from interlock.config import get_settings
from interlock.models import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    # Callers (tests, `interlock db upgrade`) may pass an explicit URL.
    url = config.attributes.get("database_url")
    return url if isinstance(url, str) else get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
