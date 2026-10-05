"""R20: pooled vs direct Neon connections. Only the last test needs the local Postgres."""

import pytest
from alembic import command
from alembic.config import Config

from app.config import get_settings
from app.db.session import connect_args

POOLED = "postgresql+psycopg://app:pw@ep-x-123-pooler.eu-central-1.aws.neon.tech/app"
DIRECT = "postgresql+psycopg://app:pw@ep-x-123.eu-central-1.aws.neon.tech/app"


def test_local_url_gets_no_extra_args():
    assert connect_args("postgresql+psycopg://app:app@localhost:5432/app") == {}


def test_remote_url_requires_ssl():
    assert connect_args(DIRECT) == {"sslmode": "require"}


def test_sslmode_in_url_wins():
    assert connect_args(DIRECT + "?sslmode=verify-full") == {}


def test_pooler_turns_off_server_side_prepared_statements():
    assert connect_args(POOLED + "?sslmode=require") == {"prepare_threshold": None}


@pytest.fixture
def env(monkeypatch):
    def set_env(**values):
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    yield set_env
    get_settings.cache_clear()


def test_migrations_refuse_the_pooler(env):
    env(DATABASE_URL=POOLED, ADMIN_DATABASE_URL="")
    with pytest.raises(RuntimeError, match="direct connection"):
        command.upgrade(Config("alembic.ini"), "head")


def test_migrations_use_admin_url_when_set(env):
    env(DATABASE_URL=POOLED, ADMIN_DATABASE_URL=get_settings().database_url)
    command.upgrade(Config("alembic.ini"), "head")  # already at head: connects, no-op
