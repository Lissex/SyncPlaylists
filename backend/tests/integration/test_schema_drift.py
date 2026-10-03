"""Схема, которую дают миграции, совпадает с ORM-моделями (аналог `alembic check`).

Почему остальные интеграционные тесты дрейф не ловят: они накатывают миграции и
проверяют ПОВЕДЕНИЕ (вставки, выборки, ограничения). Отсутствующий в ORM индекс или
NOT NULL, объявленный в модели, но не в БД, на поведение не влияет — до первого
`alembic revision --autogenerate`, который сгенерирует «лишние» изменения.
"""

import asyncio
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, NullPool, text
from sqlalchemy.ext.asyncio import create_async_engine

from syncplaylists.bootstrap.models import load_orm_models
from tests.integration.conftest import run_alembic


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{database}"))


async def _admin(url: str, sql: str) -> None:
    # CREATE/DROP DATABASE нельзя выполнять внутри транзакции — AUTOCOMMIT.
    engine = create_async_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(sql))
    finally:
        await engine.dispose()


@pytest.fixture
def clean_database_url(postgres_url: str) -> Iterator[str]:
    """Отдельная пустая БД в том же контейнере: миграции с нуля, без данных и без
    влияния на общую тестовую БД (здесь же проверяем и downgrade)."""
    name = f"schema_check_{uuid4().hex[:12]}"
    asyncio.run(_admin(postgres_url, f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(postgres_url, name)
    finally:
        asyncio.run(_admin(postgres_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _schema_diff(url: str) -> list[Any]:
    metadata = load_orm_models()

    def compare(connection: Connection) -> list[Any]:
        # Те же опции, что у `alembic check` (autogenerate в env.py): compare_type вкл.
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return list(compare_metadata(context, metadata))

    async def run() -> list[Any]:
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(compare)
        finally:
            await engine.dispose()

    return asyncio.run(run())


# Синхронные тесты намеренно: env.py сам делает asyncio.run(), а внутри уже
# запущенного event loop (async-тест) это невозможно.


def test_migrations_produce_schema_identical_to_orm_models(clean_database_url: str) -> None:
    run_alembic(clean_database_url, "upgrade", "head")

    assert _schema_diff(clean_database_url) == []


def test_downgrade_to_base_and_upgrade_again_is_clean(clean_database_url: str) -> None:
    run_alembic(clean_database_url, "upgrade", "head")
    run_alembic(clean_database_url, "downgrade", "base")
    run_alembic(clean_database_url, "upgrade", "head")

    assert _schema_diff(clean_database_url) == []
