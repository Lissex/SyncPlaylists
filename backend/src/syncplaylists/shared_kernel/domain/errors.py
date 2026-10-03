"""Ошибки площадок — часть контракта MusicPlatformGateway (порт лежит в этом же
domain-пакете). Адаптеры переводят в них HTTP-коды и исключения библиотек, чтобы
use case'ы не знали ни про httpx, ни про yandex-music."""

from syncplaylists.shared_kernel.domain.value_objects import Platform


class PlatformError(Exception):
    def __init__(self, platform: Platform, message: str = "") -> None:
        super().__init__(f"{platform.value}: {message}" if message else platform.value)
        self.platform = platform


class PlatformAuthError(PlatformError):
    """Токен не принят площадкой (401): отозван или протух."""


class PlatformRateLimitedError(PlatformError):
    """Площадка (или наш token bucket) просит подождать."""

    def __init__(self, platform: Platform, retry_after_seconds: float, message: str = "") -> None:
        super().__init__(platform, message or f"retry after {retry_after_seconds:.1f}s")
        self.retry_after_seconds = retry_after_seconds


class PlatformUnavailableError(PlatformError):
    """5xx, сеть, таймаут — временная ошибка, запрос можно повторить."""


class PlatformRegionError(PlatformError):
    """Площадка недоступна из региона, откуда идёт запрос (нужен РФ-IP и т.п.)."""


class PlaylistNotFoundError(PlatformError):
    """Плейлиста нет или он приватный и чужой."""


class PlaylistNotWritableError(PlatformError):
    """Плейлист чужой (403 на запись) — аккаунт при этом исправен."""


class PlatformNotSupportedError(Exception):
    """Для площадки (или транспорта) ещё нет адаптера."""

    def __init__(self, platform: Platform) -> None:
        super().__init__(f"Площадка {platform.value} пока не поддерживается")
        self.platform = platform


class UnsupportedLinkError(Exception):
    """Ссылка не распознана: чужой хост, не плейлист (альбом/трек), микс и т.п.
    `reason` — машиночитаемый код для API."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason
