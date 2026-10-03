import base64
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from syncplaylists.bootstrap.api import create_app
from syncplaylists.infrastructure.config.settings import Settings

# Фиксированный тестовый ключ AES-256-GCM (32 байта) — только для тестов.
TEST_SECURITY = {
    "token_encryption_key": base64.b64encode(bytes(range(32))).decode(),
    "cookie_secure": False,
}


@pytest.fixture
def settings() -> Settings:
    return Settings(
        db={"dsn": "postgresql+asyncpg://test:test@localhost:5432/test"},
        redis={"dsn": "redis://localhost:6379/0"},
        security=TEST_SECURITY,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
