import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.infrastructure.db.engine import create_engine
from syncplaylists.infrastructure.db.session import create_session_factory

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer(image="postgres:17-bookworm", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer(image="redis:7-alpine") as redis:
        host = redis.get_container_host_ip()
        port = redis.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def migrated_postgres_url(postgres_url: str) -> str:
    # env.py строит Settings() сам (единая точка конфигурации и для приложения, и для
    # миграций) — чтобы "alembic upgrade" в тесте пошёл в контейнер, а не в .env,
    # подменяем DB__DSN на время апгрейда через переменную окружения.
    previous = os.environ.get("DB__DSN")
    os.environ["DB__DSN"] = postgres_url
    try:
        command.upgrade(Config(str(_ALEMBIC_INI)), "head")
    finally:
        if previous is None:
            os.environ.pop("DB__DSN", None)
        else:
            os.environ["DB__DSN"] = previous
    return postgres_url


@pytest.fixture
def settings(migrated_postgres_url: str, redis_url: str) -> Settings:
    return Settings(db={"dsn": migrated_postgres_url}, redis={"dsn": redis_url})


@pytest.fixture
async def session(settings: Settings) -> AsyncIterator[AsyncSession]:
    engine = create_engine(settings.db)
    factory = create_session_factory(engine)
    async with factory() as db_session:
        yield db_session
        # Тест мог сам поймать ожидаемую ошибку БД (pytest.raises) и продолжить
        # без явного rollback — транзакция в сессии всё равно остаётся "aborted",
        # commit() на ней упадёт. Подчищаем здесь, а не требуем от каждого теста.
        try:
            await db_session.commit()
        except Exception:
            await db_session.rollback()
    await engine.dispose()
