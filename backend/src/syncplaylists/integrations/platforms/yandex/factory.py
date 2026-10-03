from uuid import UUID

import httpx
from yandex_music import ClientAsync

from syncplaylists.integrations.platforms.yandex.gateway import YandexGateway
from syncplaylists.integrations.platforms.yandex.transport import HttpxYandexRequest
from syncplaylists.modules.accounts.application.ports import PlatformProfile
from syncplaylists.shared_kernel.application.ports import (
    AccountAccess,
    PlatformCredentials,
    PlatformRateLimiter,
)
from syncplaylists.shared_kernel.domain.errors import PlatformAuthError
from syncplaylists.shared_kernel.domain.value_objects import Platform


class YandexClientFactory:
    """Собирает ClientAsync библиотеки с httpx-транспортом. Сеть при создании не
    трогаем (без ClientAsync.init()): uid аккаунта уже известен и проверен."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        timeout_seconds: float,
        limiter: PlatformRateLimiter | None = None,
    ) -> None:
        self._http = http
        self._timeout = timeout_seconds
        self._limiter = limiter

    def client(self, token: str, account_id: UUID | None = None) -> ClientAsync:
        request = HttpxYandexRequest(
            self._http,
            timeout_seconds=self._timeout,
            limiter=self._limiter if account_id is not None else None,
            account_id=account_id,
        )
        return ClientAsync(token, request=request)


class YandexGatewayBuilder:
    def __init__(
        self,
        clients: YandexClientFactory,
        *,
        batch_size: int,
        library_batch_preserves_order: bool = True,
    ) -> None:
        self._clients = clients
        self._batch_size = batch_size
        self._library_batch_preserves_order = library_batch_preserves_order

    def __call__(self, access: AccountAccess) -> YandexGateway:
        client = self._clients.client(access.credentials.access_token, access.account_id)
        return YandexGateway(
            client,
            access.external_user_id,
            batch_size=self._batch_size,
            library_batch_preserves_order=self._library_batch_preserves_order,
        )


class YandexProfileFetcher:
    """accounts.PlatformProfileFetcher: профиль по токену (/account/status) —
    проверка токена при подключении и перепроверка после 401."""

    platform = Platform.YANDEX

    def __init__(self, clients: YandexClientFactory) -> None:
        self._clients = clients

    async def fetch(self, credentials: PlatformCredentials) -> PlatformProfile:
        status = await self._clients.client(credentials.access_token).account_status()
        account = status.account if status is not None else None
        # Без валидного токена /account/status может ответить 200 с анонимным
        # аккаунтом (без uid) — это тоже «токен не принят».
        if account is None or account.uid is None:
            raise PlatformAuthError(Platform.YANDEX, "токен не даёт доступа к аккаунту")
        display_name = account.display_name or account.full_name or account.login
        return PlatformProfile(external_user_id=str(account.uid), display_name=display_name)
