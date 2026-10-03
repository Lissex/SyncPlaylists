from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import UUID

import httpx

from syncplaylists.integrations.platforms.soundcloud.api import OfficialApi, SoundCloudApi, V2Api
from syncplaylists.integrations.platforms.soundcloud.client_id import ClientIdProvider
from syncplaylists.integrations.platforms.soundcloud.gateway import SoundCloudGateway
from syncplaylists.integrations.platforms.soundcloud.tokens import (
    AccountTokens,
    TokenEndpoint,
    token_client_id,
    token_expires_at,
)
from syncplaylists.integrations.platforms.soundcloud.transport import (
    OFFICIAL_BASE_URL,
    V2_BASE_URL,
    SoundCloudTransport,
)
from syncplaylists.modules.accounts.application.ports import OAuthGrant, PlatformProfile
from syncplaylists.shared_kernel.application.ports import (
    AccountAccess,
    CredentialsRefresher,
    PlatformCredentials,
    PlatformRateLimiter,
)
from syncplaylists.shared_kernel.domain.errors import (
    PlatformAuthError,
    PlatformNotSupportedError,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport

AUTHORIZE_URL = "https://secure.soundcloud.com/authorize"


@dataclass(frozen=True, slots=True)
class OfficialApp:
    """Приложение в официальном API (Settings.platforms.soundcloud.official)."""

    client_id: str
    client_secret: str


@dataclass(frozen=True, slots=True)
class SoundCloudLimits:
    tracks_batch_size: int = 50
    likes_page_size: int = 200
    playlist_max_tracks: int = 500


class SoundCloudApiFactory:
    """Собирает транспорт и API под транспорт аккаунта. Сеть при создании не трогаем."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        client_ids: ClientIdProvider,
        token_endpoint: TokenEndpoint,
        *,
        timeout_seconds: float,
        limiter: PlatformRateLimiter | None = None,
        refresher: CredentialsRefresher | None = None,
        official: OfficialApp | None = None,
    ) -> None:
        self._http = http
        self._client_ids = client_ids
        self._token_endpoint = token_endpoint
        self._timeout = timeout_seconds
        self._limiter = limiter
        self._refresher = refresher
        self._official = official

    def for_account(self, access: AccountAccess) -> SoundCloudApi:
        if access.transport is Transport.OFFICIAL:
            official = self._official
            if official is None:
                # Аккаунт подключён через OAuth, а приложение из настроек убрали.
                raise PlatformNotSupportedError(Platform.SOUNDCLOUD)
            tokens = self._tokens(access, official.client_secret, self._official_client_id)
            return OfficialApi(self._transport(OFFICIAL_BASE_URL, None, tokens, access.account_id))
        tokens = self._tokens(access, None, self._web_client_id)
        return V2Api(self._transport(V2_BASE_URL, self._client_ids, tokens, access.account_id))

    def for_token(self, token: str, transport: Transport = Transport.UNOFFICIAL) -> SoundCloudApi:
        """Без аккаунта и продления — проверка профиля при подключении."""
        if transport is Transport.OFFICIAL:
            return OfficialApi(self._transport(OFFICIAL_BASE_URL, None, None, None, token))
        return V2Api(self._transport(V2_BASE_URL, self._client_ids, None, None, token))

    def _tokens(
        self,
        access: AccountAccess,
        client_secret: str | None,
        client_id_for: Callable[[str], Awaitable[str | None]],
    ) -> AccountTokens:
        return AccountTokens(
            access,
            endpoint=self._token_endpoint,
            refresher=self._refresher,
            refresh_client_id=client_id_for,
            client_secret=client_secret,
        )

    async def _web_client_id(self, token: str) -> str | None:
        # Сайт продлевает токен с client_id из claims самого токена; запасной — текущий
        # client_id веб-клиента.
        return token_client_id(token) or await self._client_ids.get()

    async def _official_client_id(self, token: str) -> str | None:
        return self._official.client_id if self._official is not None else None

    def _transport(
        self,
        base_url: str,
        client_ids: ClientIdProvider | None,
        tokens: AccountTokens | None,
        account_id: UUID | None,
        static_token: str | None = None,
    ) -> SoundCloudTransport:
        return SoundCloudTransport(
            self._http,
            base_url=base_url,
            timeout_seconds=self._timeout,
            client_ids=client_ids,
            tokens=tokens,
            limiter=self._limiter,
            account_id=account_id,
            static_token=static_token,
        )


class SoundCloudGatewayBuilder:
    def __init__(self, apis: SoundCloudApiFactory, limits: SoundCloudLimits) -> None:
        self._apis = apis
        self._limits = limits

    def __call__(self, access: AccountAccess) -> SoundCloudGateway:
        return SoundCloudGateway(
            self._apis.for_account(access),
            access.external_user_id,
            tracks_batch_size=self._limits.tracks_batch_size,
            likes_page_size=self._limits.likes_page_size,
            playlist_max_tracks=self._limits.playlist_max_tracks,
        )


def _profile(me: dict[str, object]) -> PlatformProfile:
    user_id = me.get("id")
    if user_id is None:
        raise PlatformAuthError(Platform.SOUNDCLOUD, "токен не даёт доступа к аккаунту")
    name = me.get("username") or me.get("full_name") or me.get("permalink")
    return PlatformProfile(external_user_id=str(user_id), display_name=str(name) if name else None)


class SoundCloudProfileFetcher:
    """accounts.PlatformProfileFetcher: профиль по токену (/me) — проверка токена при
    подключении и перепроверка после 401. Токен из cookie сайта — для v2."""

    platform = Platform.SOUNDCLOUD

    def __init__(self, apis: SoundCloudApiFactory) -> None:
        self._apis = apis

    async def fetch(self, credentials: PlatformCredentials) -> PlatformProfile:
        return _profile(await self._apis.for_token(credentials.access_token).me())


class SoundCloudOAuthProvider:
    """accounts.OAuthProvider для официального API: OAuth 2.1 + PKCE (S256), state и
    PKCE готовит StartOAuthUseCase. Регистрируется, только если в Settings задано
    приложение (Artist Pro у разработчика)."""

    platform = Platform.SOUNDCLOUD

    def __init__(
        self, app: OfficialApp, token_endpoint: TokenEndpoint, apis: SoundCloudApiFactory
    ) -> None:
        self._app = app
        self._token_endpoint = token_endpoint
        self._apis = apis

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        query = {
            "client_id": self._app.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urlencode(query)}"

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthGrant:
        credentials = await self._token_endpoint.exchange_code(
            code=code,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            client_id=self._app.client_id,
            client_secret=self._app.client_secret,
        )
        api = self._apis.for_token(credentials.access_token, Transport.OFFICIAL)
        profile = _profile(await api.me())
        return OAuthGrant(
            external_user_id=profile.external_user_id,
            display_name=profile.display_name,
            access_token=credentials.access_token,
            refresh_token=credentials.refresh_token,
            expires_at=credentials.expires_at or token_expires_at(credentials.access_token),
        )
