"""HTTP-транспорт SoundCloud (api-v2 и официальный api.soundcloud.com).

Весь трафик к API идёт через одно место: token bucket перед каждым запросом (включая
пагинацию и догрузку треков), счётчик запросов сервера, общие таймауты, перевод
HTTP-кодов в доменные ошибки и respx в тестах.

401 токена ≠ 401 client_id. У v2 оба выглядят одинаково — пустой 401 (проверено
2026-10-03: и битый client_id, и битый токен). Поэтому на 401 сначала пробуем обновить
client_id (не чаще раза в минуту, см. ClientIdProvider.refresh) и повторяем запрос;
затем, если есть refresh_token, — продлеваем токен и повторяем. 401 после этого — токен
(PlatformAuthError → перепроверка через /me → EXPIRED).
"""

import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Final
from uuid import UUID

import httpx

from syncplaylists.integrations.platforms.rate_limit_log import log_rate_limited
from syncplaylists.integrations.platforms.soundcloud.client_id import (
    BROWSER_USER_AGENT,
    ClientIdProvider,
)
from syncplaylists.integrations.platforms.soundcloud.tokens import AccountTokens
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

_PLATFORM: Final = Platform.SOUNDCLOUD
V2_BASE_URL: Final = "https://api-v2.soundcloud.com"
OFFICIAL_BASE_URL: Final = "https://api.soundcloud.com"
_DEFAULT_RETRY_AFTER_SECONDS: Final = 60.0
_MAX_RETRY_AFTER_SECONDS: Final = 24 * 3600.0


class SoundCloudForbiddenError(PlatformError):
    """403. Что он значит, понятно только по контексту: запись в чужой сет —
    PlaylistNotWritableError, чтение закрытого — PlaylistNotFoundError. Протухший токен
    SoundCloud отдаёт как 401, поэтому 403 НЕ переводит аккаунт в EXPIRED."""


class SoundCloudBadRequestError(PlatformError):
    """Прочие 4xx (400, 422) — запрос не принят. Не временная."""


class SoundCloudTransport:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        base_url: str,
        timeout_seconds: float,
        client_ids: ClientIdProvider | None = None,
        tokens: AccountTokens | None = None,
        limiter: PlatformRateLimiter | None = None,
        account_id: UUID | None = None,
        static_token: str | None = None,
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        # None — официальный API: ему client_id в запросе не нужен.
        self._client_ids = client_ids
        self._tokens = tokens
        # Токен без продления — проверка профиля при подключении.
        self._static_token = static_token
        self._limiter = limiter
        self._account_id = account_id

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """path — путь API ("/me") или полный next_href пагинации (он уже содержит
        параметры, кроме client_id). Возвращает разобранный JSON (None — пустой ответ)."""
        token = await self._token()
        client_id = await self._client_ids.get() if self._client_ids is not None else None
        response = await self._send(method, path, params, json, token, client_id)

        if response.status_code == 401 and self._client_ids is not None and client_id:
            fresh = await self._client_ids.refresh(client_id)
            if fresh is not None:
                client_id = fresh
                response = await self._send(method, path, params, json, token, client_id)
        if (
            response.status_code == 401
            and self._tokens is not None
            and token is not None
            and await self._tokens.after_unauthorized(token)
        ):
            token = await self._tokens.current()
            response = await self._send(method, path, params, json, token, client_id)

        if response.is_success:
            return response.json() if response.content else None
        error = _error_for(response)
        if isinstance(error, PlatformRateLimitedError) and self._limiter is not None:
            if self._account_id is not None:
                # Пауза на весь аккаунт: параллельные задачи будут ждать в token bucket
                # (или сразу уйдут в повтор с тем же сроком), а не соберут по 429 каждая.
                await self._limiter.penalize(_PLATFORM, self._account_id, error.retry_after_seconds)
            await log_rate_limited(
                logger,
                self._limiter,
                _PLATFORM,
                self._account_id,
                error.retry_after_seconds,
                _rate_limit_bucket(response),
            )
        raise error

    async def count_request(self) -> None:
        """Учёт запроса с сервера (IP) — и для запросов не к API (сайт, oauth)."""
        if self._limiter is None:
            return
        try:
            await self._limiter.count_request(_PLATFORM)
        except Exception as exc:  # статистика не должна мешать запросу
            logger.debug("Счётчик запросов недоступен: %s", type(exc).__name__)

    async def _token(self) -> str | None:
        if self._tokens is not None:
            return await self._tokens.current()
        return self._static_token

    async def _send(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json: Any,
        token: str | None,
        client_id: str | None,
    ) -> httpx.Response:
        if self._limiter is not None and self._account_id is not None:
            await self._limiter.acquire(_PLATFORM, self._account_id)
        await self.count_request()
        if path.startswith("https://"):
            # next_href пагинации: его параметры (offset, cursor) сохраняем — httpx
            # заменил бы query адреса целиком на params.
            full = httpx.URL(path)
            url = str(full.copy_with(query=None))
            query: dict[str, Any] = dict(full.params)
            query.update(params or {})
        else:
            url = f"{self._base_url}{path}"
            query = dict(params or {})
        if client_id is not None:
            query["client_id"] = client_id
        headers = {"User-Agent": BROWSER_USER_AGENT, "Accept": "application/json; charset=utf-8"}
        if token is not None:
            headers["Authorization"] = f"OAuth {token}"
        try:
            return await self._http.request(
                method, url, params=query, json=json, headers=headers, timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            raise PlatformUnavailableError(_PLATFORM, "таймаут") from exc
        except httpx.HTTPError as exc:
            raise PlatformUnavailableError(_PLATFORM, type(exc).__name__) from exc


def _is_bot_challenge(response: httpx.Response) -> bool:
    # DataDome (антибот SoundCloud) отвечает 403 с HTML-капчей; вероятнее всего с IP
    # датацентров. Это не «нет прав» и не протухший client_id — временная ошибка.
    if "x-datadome" in response.headers:
        return True
    content_type = response.headers.get("content-type", "")
    return "text/html" in content_type and b"captcha-delivery.com" in response.content


def _json_errors(response: httpx.Response) -> list[dict[str, Any]]:
    try:
        body = response.json()
    except ValueError:
        return []
    errors = body.get("errors") if isinstance(body, dict) else None
    return [e for e in errors if isinstance(e, dict)] if isinstance(errors, list) else []


def _rate_limit_meta(response: httpx.Response) -> dict[str, Any]:
    for error in _json_errors(response):
        meta = error.get("meta")
        if isinstance(meta, dict):
            return meta
    return {}


def _rate_limit_bucket(response: httpx.Response) -> str:
    # "by-client" — квота общего client_id сайта (одна на весь сервер), не аккаунта.
    rate_limit = _rate_limit_meta(response).get("rate_limit")
    if isinstance(rate_limit, dict) and rate_limit.get("bucket"):
        return f"bucket {rate_limit['bucket']}"
    return ""


def _retry_after(response: httpx.Response, now: datetime | None = None) -> float:
    header = response.headers.get("retry-after")
    if header:
        try:
            return max(float(header), 1.0)
        except ValueError:
            pass
    reset_time = _rate_limit_meta(response).get("reset_time")
    if isinstance(reset_time, str):
        try:
            reset = datetime.strptime(reset_time, "%Y/%m/%d %H:%M:%S %z")
        except ValueError:
            try:
                reset = parsedate_to_datetime(reset_time)
            except (TypeError, ValueError):
                reset = None
        if reset is not None:
            seconds = (reset - (now or datetime.now(UTC))).total_seconds()
            return min(max(seconds, 1.0), _MAX_RETRY_AFTER_SECONDS)
    return _DEFAULT_RETRY_AFTER_SECONDS


def _error_for(response: httpx.Response) -> PlatformError:
    status = response.status_code
    message = f"HTTP {status}"
    if status == 401:
        return PlatformAuthError(_PLATFORM, message)
    if status == 403:
        if _is_bot_challenge(response):
            logger.warning("SoundCloud: антибот (DataDome) на запрос — повтор позже")
            return PlatformUnavailableError(_PLATFORM, "антибот")
        return SoundCloudForbiddenError(_PLATFORM, message)
    if status == 404:
        return PlaylistNotFoundError(_PLATFORM, message)
    if status == 429:
        return PlatformRateLimitedError(_PLATFORM, _retry_after(response), message)
    if status == 451:
        return PlatformRegionError(_PLATFORM, message)
    if status >= 500:
        return PlatformUnavailableError(_PLATFORM, message)
    return SoundCloudBadRequestError(_PLATFORM, message)
