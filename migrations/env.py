import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import drift.persistence.models  # noqa: F401  (register tables on Base.metadata)
from drift.persistence.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
# DATABASE_URL wins over alembic.ini, matching drift.persistence.db — so the live DB
# (drift_live) and the test/dev default can be migrated with the same command.
if os.environ.get("DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
target_metadata = Base.metadata


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against the live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
