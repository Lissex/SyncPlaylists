"""Реестр операций, которые бэкенд может поручить расширению. Расширение выполняет
только операции из своего такого же фиксированного реестра — произвольных запросов
сервер ему не шлёт (ARCHITECTURE.md, 11h)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from syncplaylists.shared_kernel.domain.errors import (
    ExtensionUnavailableError,
    ExtensionUnavailableReason,
    PlatformError,
    PlatformRateLimitedError,
    PlatformUnavailableError,
    PlaylistNotFoundError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform


@dataclass(frozen=True, slots=True)
class OperationSpec:
    """timeout_seconds — сколько ждём результат (и на сколько отодвигает дедлайн каждое
    сообщение progress от расширения); per_item_seconds — добавка на трек для записей,
    которые расширение делает в темпе ≤ 1 запрос/с; write — операция меняет данные на
    площадке (её результат расширение кладёт в журнал)."""

    timeout_seconds: float
    per_item_seconds: float = 0.0
    write: bool = False

    def timeout_for(self, items: int) -> float:
        return self.timeout_seconds + self.per_item_seconds * max(items, 0)


# Длинные чтения — постранично: одна задача = одна страница (playlist_page, library_page),
# чтобы таймаут не рос с размером медиатеки.
OPERATIONS: Final[Mapping[str, OperationSpec]] = {
    "search": OperationSpec(timeout_seconds=30),
    "search_by_isrc": OperationSpec(timeout_seconds=30),
    "playlist_info": OperationSpec(timeout_seconds=30),
    "is_own_library": OperationSpec(timeout_seconds=30),
    "playlist_page": OperationSpec(timeout_seconds=60),
    "library_page": OperationSpec(timeout_seconds=60),
    "create_playlist": OperationSpec(timeout_seconds=30, write=True),
    "add_tracks": OperationSpec(timeout_seconds=30, per_item_seconds=1.5, write=True),
    "add_to_library": OperationSpec(timeout_seconds=30, per_item_seconds=1.5, write=True),
}


def operation_spec(operation: str) -> OperationSpec:
    spec = OPERATIONS.get(operation)
    if spec is None:
        raise ValueError(f"Неизвестная операция расширения: {operation}")
    return spec


def wire_op(platform: Platform, operation: str) -> str:
    return f"{platform.value}.{operation}"


_UNAVAILABLE_CODES: Final = {
    "logged_out": ExtensionUnavailableReason.LOGGED_OUT,
    "session_mismatch": ExtensionUnavailableReason.SESSION_MISMATCH,
    "captcha": ExtensionUnavailableReason.CAPTCHA,
    "no_permission": ExtensionUnavailableReason.NO_PERMISSION,
}


def error_from_wire(platform: Platform, error: Mapping[str, Any]) -> PlatformError:
    """Ошибка, о которой сообщило расширение (поле error результата), → ошибка
    shared_kernel. Неизвестный код — временная ошибка площадки (повтор)."""
    code = str(error.get("code", ""))
    message = str(error.get("message", ""))[:200]
    reason = _UNAVAILABLE_CODES.get(code)
    if reason is not None:
        return ExtensionUnavailableError(platform, reason)
    if code == "not_found":
        return PlaylistNotFoundError(platform, message)
    if code == "not_writable":
        return PlaylistNotWritableError(platform, message)
    if code == "rate_limited":
        retry_after = error.get("retry_after")
        seconds = float(retry_after) if isinstance(retry_after, int | float) else 60.0
        return PlatformRateLimitedError(platform, max(seconds, 1.0), message)
    return PlatformUnavailableError(platform, f"расширение: {code or 'error'} {message}".strip())
