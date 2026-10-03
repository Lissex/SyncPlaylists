import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.infrastructure.db.engine import create_engine
from syncplaylists.infrastructure.db.session import create_session_factory
from syncplaylists.modules.identity.infrastructure.orm import UserOrm
from syncplaylists.shared_kernel.domain.value_objects import Platform
from tests.conftest import TEST_SECURITY

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


def run_alembic(database_url: str, action: str, revision: str) -> None:
    """`alembic upgrade|downgrade <revision>` против указанной БД.

    env.py строит Settings() сам (единая точка конфигурации и для приложения, и для
    миграций) — чтобы миграции пошли в тестовую БД, а не в .env, подменяем DB__DSN (и
    обязательный ключ шифрования — на случай, если .env нет) через окружение."""
    overrides = {
        "DB__DSN": database_url,
        "SECURITY__TOKEN_ENCRYPTION_KEY": str(TEST_SECURITY["token_encryption_key"]),
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        getattr(command, action)(Config(str(_ALEMBIC_INI)), revision)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="session")
def migrated_postgres_url(postgres_url: str) -> str:
    run_alembic(postgres_url, "upgrade", "head")
    return postgres_url


@pytest.fixture
def settings(migrated_postgres_url: str, redis_url: str) -> Settings:
    return Settings(
        db={"dsn": migrated_postgres_url},
        redis={"dsn": redis_url},
        security=TEST_SECURITY,
        oauth={"fake_platforms": [Platform.SPOTIFY]},
        # Яндекс — настоящий адаптер (его HTTP в тестах подменяет respx), остальные
        # площадки — фейк.
        platforms={"fake": [Platform.VK, Platform.SPOTIFY, Platform.SOUNDCLOUD, Platform.YTMUSIC]},
    )


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


@pytest.fixture
async def user_id(session: AsyncSession) -> UUID:
    """Пользователь в той же сессии/транзакции, что и тест: с этапа 4a transfers.user_id
    и connected_accounts.user_id — FK на users."""
    new_id = uuid4()
    session.add(
        UserOrm(
            id=new_id,
            email=f"{new_id.hex}@example.com",
            password_hash="x",
            created_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return new_id
