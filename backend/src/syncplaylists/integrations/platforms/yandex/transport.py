"""HTTP-транспорт для yandex-music: вместо встроенного aiohttp — общий httpx-клиент.

Так весь трафик к Яндексу проходит через одно место: token bucket перед каждым
запросом (включая пагинацию и догрузку треков), общие таймауты, перевод HTTP-кодов в
доменные ошибки (а не в UnauthorizedError библиотеки, где 401 и 403 слиты в одно),
и respx в тестах.
"""

import json
import logging
from typing import Any, Final
from uuid import UUID

import httpx
from yandex_music.utils.request_async import Request
from yandex_music.utils.request_base import RequestBase

from syncplaylists.integrations.platforms.rate_limit_log import log_rate_limited
from syncplaylists.shared_kernel.application.ports import PlatformRateLimiter
from syncplaylists.shared_kernel.domain.errors import (
    PlatformAuthError,
    PlatformError,
    PlatformRateLimitedError,
    PlatformRegionError,
    PlatformUnavailableError,
    PlaylistNotFoundError,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform

logger = logging.getLogger(__name__)

_PLATFORM: Final = Platform.YANDEX
_DEFAULT_RETRY_AFTER_SECONDS: Final = 5.0


class YandexForbiddenError(PlatformError):
    """403. Что он значит, понятно только по контексту: запись в чужой плейлист —
    PlaylistNotWritableError, чтение закрытого — PlaylistNotFoundError. Протухший
    токен Яндекс отдаёт как 401, поэтому 403 НЕ переводит аккаунт в EXPIRED."""


class YandexWrongRevisionError(PlatformError):
    """Плейлист изменился с момента чтения (diff применяется к ревизии) — перечитать
    и повторить."""


class YandexBadRequestError(PlatformError):
    """400 — запрос не принят (например, вставка трека без альбома). Не временная."""


class HttpxYandexRequest(Request):  # type: ignore[misc]  # yandex-music без аннотаций
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        timeout_seconds: float,
        limiter: PlatformRateLimiter | None = None,
        account_id: UUID | None = None,
    ) -> None:
        super().__init__(timeout=timeout_seconds)
        self._http = http
        self._limiter = limiter
        self._account_id = account_id

    async def _request_wrapper(self, *args: Any, **kwargs: Any) -> bytes:
        method, url = args[0], args[1]
        # Базовая подготовка (User-Agent, таймаут по умолчанию) — без aiohttp-обёртки
        # таймаута из async-подкласса библиотеки.
        kwargs = RequestBase._prepare_kwargs(self, kwargs)
        if self._limiter is not None and self._account_id is not None:
            await self._limiter.acquire(_PLATFORM, self._account_id)
        await self._count_request()
        try:
            response = await self._http.request(
                method,
                url,
                params=kwargs.get("params"),
                data=kwargs.get("data"),
                json=kwargs.get("json"),
                headers=kwargs.get("headers"),
                timeout=kwargs["timeout"],
            )
        except httpx.TimeoutException as exc:
            raise PlatformUnavailableError(_PLATFORM, "таймаут") from exc
        except httpx.HTTPError as exc:
            raise PlatformUnavailableError(_PLATFORM, type(exc).__name__) from exc

        if response.is_success:
            return response.content
        error = _error_for(response)
        if isinstance(error, PlatformRateLimitedError) and self._limiter is not None:
            if self._account_id is not None:
                # Пауза на весь аккаунт: параллельные задачи будут ждать в token bucket
                # (или сразу уйдут в повтор с тем же сроком), а не соберут по 429 каждая.
                await self._limiter.penalize(_PLATFORM, self._account_id, error.retry_after_seconds)
            await log_rate_limited(
                logger, self._limiter, _PLATFORM, self._account_id, error.retry_after_seconds
            )
        raise error

    async def _count_request(self) -> None:
        if self._limiter is None:
            return
        try:
            await self._limiter.count_request(_PLATFORM)
        except Exception as exc:  # статистика не должна мешать запросу
            logger.debug("Счётчик запросов недоступен: %s", type(exc).__name__)


def _error_name(response: httpx.Response) -> str:
    try:
        body = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("name") or error.get("message") or "")
    return str(error or "")


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(float(response.headers.get("retry-after", "")), 1.0)
    except ValueError:
        return _DEFAULT_RETRY_AFTER_SECONDS


def _error_for(response: httpx.Response) -> PlatformError:
    status = response.status_code
    name = _error_name(response)
    message = f"HTTP {status} {name}".strip()
    if "wrong-revision" in name:
        return YandexWrongRevisionError(_PLATFORM, message)
    if status == 401:
        return PlatformAuthError(_PLATFORM, message)
    if status == 403:
        return YandexForbiddenError(_PLATFORM, message)
    if status == 404:
        return PlaylistNotFoundError(_PLATFORM, message)
    if status == 429:
        return PlatformRateLimitedError(_PLATFORM, _retry_after(response), message)
    if status == 451:
        return PlatformRegionError(_PLATFORM, message)
    if status >= 500:
        return PlatformUnavailableError(_PLATFORM, message)
    return YandexBadRequestError(_PLATFORM, message)
