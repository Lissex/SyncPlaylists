"""Live-тесты ходят в настоящие площадки с токеном владельца репозитория.

Запуск только явно: `uv run pytest -m live -s` (по умолчанию addopts = "-m 'not live'").
Без токена в .env тесты пропускаются. Токен читается через pydantic-settings, как и
вся остальная конфигурация, — без os.getenv.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LiveSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    yandex_live_token: SecretStr | None = None


@pytest.fixture(scope="session")
def live_settings() -> LiveSettings:
    return LiveSettings()


@pytest.fixture
async def live_http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client
