from alembic import command
from alembic.config import Config


def alembic_config(database_url: str | None = None) -> Config:
    """Alembic config that works from a source checkout or an installed package."""
    config = Config()
    config.set_main_option("script_location", "ringkeeper:migrations")
    if database_url is not None:
        config.attributes["database_url"] = database_url
    return config


def upgrade(database_url: str | None = None, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str | None = None, revision: str = "base") -> None:
    command.downgrade(alembic_config(database_url), revision)
