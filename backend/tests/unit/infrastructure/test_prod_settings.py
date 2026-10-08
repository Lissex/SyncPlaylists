"""env=prod не запускается с dev-настройками расширения (fail-fast при чтении Settings)."""

from typing import Any

import pytest
from pydantic import ValidationError

from syncplaylists.infrastructure.config.settings import Settings

_BASE: dict[str, Any] = {
    "db": {"dsn": "postgresql+asyncpg://u:p@localhost:5432/db"},
    "redis": {"dsn": "redis://localhost:6379/0"},
    "security": {"token_encryption_key": "A" * 43 + "="},
}
_SAFE_EXTENSION: dict[str, Any] = {"allowed_extension_ids": ["abcdefghijklmnopabcdefghijklmnop"]}


def _settings(env: str, extension: dict[str, Any]) -> Settings:
    # Без .env: локальный dev-.env (EXTENSION__DEV_PAGE_ENABLED=true) не должен подмешиваться.
    return Settings(_env_file=None, **{**_BASE, "env": env, "extension": extension})


def test_prod_with_safe_extension_settings_starts() -> None:
    assert _settings("prod", _SAFE_EXTENSION).env == "prod"


@pytest.mark.parametrize(
    ("extension", "problem"),
    [
        ({}, "EXTENSION__ALLOWED_EXTENSION_IDS"),
        ({**_SAFE_EXTENSION, "dev_page_enabled": True}, "EXTENSION__DEV_PAGE_ENABLED"),
        ({**_SAFE_EXTENSION, "diagnostics_enabled": True}, "EXTENSION__DIAGNOSTICS_ENABLED"),
    ],
)
def test_prod_refuses_dev_extension_settings(extension: dict[str, Any], problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        _settings("prod", extension)


def test_dev_allows_dev_extension_settings() -> None:
    settings = _settings("dev", {"dev_page_enabled": True, "diagnostics_enabled": True})
    assert settings.extension.allowed_extension_ids == []
