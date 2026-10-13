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
    # Без .env: локальные dev-настройки разработчика не должны подмешиваться.
    return Settings(_env_file=None, **{**_BASE, "env": env, "extension": extension})


def test_prod_with_safe_extension_settings_starts() -> None:
    assert _settings("prod", _SAFE_EXTENSION).env == "prod"


@pytest.mark.parametrize(
    ("extension", "problem"),
    [
        ({}, "EXTENSION__ALLOWED_EXTENSION_IDS"),
    ],
)
def test_prod_refuses_dev_extension_settings(extension: dict[str, Any], problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        _settings("prod", extension)


def test_dev_allows_empty_extension_allowlist() -> None:
    settings = _settings("dev", {})
    assert settings.extension.allowed_extension_ids == []
