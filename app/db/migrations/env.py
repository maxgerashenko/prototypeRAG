"""Alembic environment. Migrations use ADMIN_DATABASE_URL when set: in cloud Neon's direct
connection (R20), from stage 3 also the table owner role. Locally DATABASE_URL is enough."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, make_url

from app.config import get_settings
from app.db.models import Base
from app.db.session import connect_args

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    settings = get_settings()
    return settings.admin_database_url or settings.database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _url()
    if "-pooler." in (make_url(url).host or ""):
        # The pooler runs PgBouncer in transaction mode: no session state (session SET,
        # advisory locks) survives between transactions, and long DDL holds a shared slot.
        raise RuntimeError(
            "Migrations need Neon's direct connection, not the -pooler host (R20): "
            "set ADMIN_DATABASE_URL to the direct connection string."
        )
    engine = create_engine(url, connect_args=connect_args(url))
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
