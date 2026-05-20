"""Alembic env: URL берём из приложения (`config.DB_URL`), а не из alembic.ini.

Это держит истину про БД в одном месте — `src/goszakup/config.py` уже умеет
читать `.env`. Когда переедем на Postgres, достаточно будет поменять там.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from goszakup.config import DB_URL
from goszakup.db.models import Base

config = context.config

# URL приходит из приложения, не из alembic.ini — там оставлен dummy-значение.
config.set_main_option("sqlalchemy.url", DB_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite не умеет ALTER COLUMN — Alembic эмулирует через batch.
        # На Postgres-этапе можно будет убрать (или оставить, не мешает).
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
