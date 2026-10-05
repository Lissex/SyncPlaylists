"""Токены пользователя SoundCloud: срок жизни и обновление.

Веб-токен (`oauth_token` из cookie сайта) — JWT со сроком (`exp`) и client_id
приложения-сайта в claims. Сайт продлевает его сам: httpOnly-cookie
`oauth_refresh_token` → `POST https://secure.soundcloud.com/oauth/token`
(grant_type=refresh_token, client_id из claims) — проверено по JS сайта 2026-10-03.
Официальный OAuth (OFFICIAL) использует тот же сервер авторизации, но со своим
client_id/secret. Поэтому обновление общее для обоих транспортов; без refresh_token
токен работает, пока не истечёт.

Подпись JWT мы не проверяем: claims нужны только чтобы знать срок и client_id, а
принимает ли токен SoundCloud — решает сам SoundCloud (/me).
"""

import base64
import binascii
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx

from syncplaylists.shared_kernel.application.ports import (
    AccountAccess,
    CredentialsRefresher,
    PlatformCredentials,
)
from syncplaylists.shared_kernel.domain.errors import (
    PlatformAuthError,
    PlatformRateLimitedError,
    PlatformUnavailableError,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform

logger = logging.getLogger(__name__)

_PLATFORM: Final = Platform.SOUNDCLOUD
TOKEN_URL: Final = "https://secure.soundcloud.com/oauth/token"
# Обновляем заранее, чтобы запрос не ушёл с токеном, истекающим «на лету».
_REFRESH_AHEAD: Final = timedelta(seconds=60)


def jwt_claims(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None  # старый формат "2-xxxxxx-<id>-xxxx" — не JWT
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, ValueError):
        return None
    return claims if isinstance(claims, dict) else None


def token_expires_at(token: str) -> datetime | None:
    claims = jwt_claims(token)
    exp = claims.get("exp") if claims else None
    if isinstance(exp, int | float):
        return datetime.fromtimestamp(exp, UTC)
    return None


def token_client_id(token: str) -> str | None:
    claims = jwt_claims(token)
    client_id = claims.get("client_id") if claims else None
    return client_id if isinstance(client_id, str) and client_id else None


class TokenEndpoint:
    """secure.soundcloud.com/oauth/token: обмен кода (OFFICIAL) и refresh (оба транспорта)."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        timeout_seconds: float,
        on_request: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._http = http
        self._timeout = timeout_seconds
        self._on_request = on_request
        self._clock = clock

    async def refresh(
        self, refresh_token: str, *, client_id: str, client_secret: str | None = None
    ) -> PlatformCredentials:
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if client_secret is not None:
            form["client_secret"] = client_secret
        return self._credentials(await self._post(form), fallback_refresh=refresh_token)

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
    ) -> PlatformCredentials:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        return self._credentials(await self._post(form), fallback_refresh=None)

    async def _post(self, form: dict[str, str]) -> dict[str, Any]:
        if self._on_request is not None:
            await self._on_request()
        try:
            response = await self._http.post(
                TOKEN_URL,
                data=form,
                headers={"Accept": "application/json; charset=utf-8"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise PlatformUnavailableError(_PLATFORM, f"oauth: {type(exc).__name__}") from exc
        if response.status_code == 429:
            raise PlatformRateLimitedError(_PLATFORM, 60.0, "oauth: HTTP 429")
        if response.status_code >= 500:
            raise PlatformUnavailableError(_PLATFORM, f"oauth: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not response.is_success or not isinstance(body, dict) or "access_token" not in body:
            # invalid_grant/invalid_client — refresh_token отозван, протух или уже
            # использован (ротация): без пользователя тут ничего не сделать.
            error = body.get("error", "") if isinstance(body, dict) else ""
            raise PlatformAuthError(_PLATFORM, f"oauth: HTTP {response.status_code} {error}")
        return body

    def _credentials(
        self, body: dict[str, Any], *, fallback_refresh: str | None
    ) -> PlatformCredentials:
        access_token = str(body["access_token"])
        expires_at = token_expires_at(access_token)
        expires_in = body.get("expires_in")
        if expires_at is None and isinstance(expires_in, int | float):
            expires_at = self._clock() + timedelta(seconds=expires_in)
        refresh_token = body.get("refresh_token")
        return PlatformCredentials(
            access_token=access_token,
            # Не все ответы ротируют refresh_token — тогда старый в силе.
            refresh_token=str(refresh_token) if refresh_token else fallback_refresh,
            expires_at=expires_at,
        )


class AccountTokens:
    """Токен одного аккаунта для транспорта: отдаёт текущий, заранее продлевает
    истекающий и продлевает после 401. Новые токены сохраняет CredentialsRefresher
    (под блокировкой строки аккаунта — без гонок между воркерами)."""

    def __init__(
        self,
        access: AccountAccess,
        *,
        endpoint: TokenEndpoint,
        refresher: CredentialsRefresher | None,
        refresh_client_id: Callable[[str], Awaitable[str | None]],
        client_secret: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._account_id = access.account_id
        self._credentials = access.require_credentials()
        self._endpoint = endpoint
        self._refresher = refresher
        self._refresh_client_id = refresh_client_id
        self._client_secret = client_secret
        self._clock = clock

    @property
    def can_refresh(self) -> bool:
        return self._refresher is not None and self._credentials.refresh_token is not None

    async def current(self) -> str:
        expires_at = self._credentials.expires_at or token_expires_at(
            self._credentials.access_token
        )
        if (
            self.can_refresh
            and expires_at is not None
            and (expires_at - self._clock() <= _REFRESH_AHEAD)
        ):
            await self._refresh()
        return self._credentials.access_token

    async def after_unauthorized(self, used: str) -> bool:
        """401 с токеном `used`. True — токен продлён, запрос стоит повторить."""
        if not self.can_refresh:
            return False
        if self._credentials.access_token != used:
            return True  # уже продлён параллельным запросом этого шлюза
        await self._refresh()
        return self._credentials.access_token != used

    async def _refresh(self) -> None:
        assert self._refresher is not None
        stale = self._credentials

        async def renew(current: PlatformCredentials) -> PlatformCredentials:
            assert current.refresh_token is not None
            client_id = await self._refresh_client_id(current.access_token)
            if client_id is None:
                raise PlatformAuthError(_PLATFORM, "не известен client_id для refresh")
            logger.info("SoundCloud: продление токена аккаунта %s", self._account_id)
            return await self._endpoint.refresh(
                current.refresh_token, client_id=client_id, client_secret=self._client_secret
            )

        self._credentials = await self._refresher.refresh(self._account_id, stale, renew)
