from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from syncplaylists.bootstrap.api import create_app
from syncplaylists.infrastructure.config.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        db={"dsn": "postgresql+asyncpg://test:test@localhost:5432/test"},
        redis={"dsn": "redis://localhost:6379/0"},
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
