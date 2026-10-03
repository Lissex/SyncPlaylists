"""Общее для тестов адаптера SoundCloud: respx вместо сети, фикстуры
tests/fixtures/soundcloud (форма ответов api-v2, данные вымышленные), фейки лимитера,
кэша client_id и сохранения токенов. Без Docker."""

import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import respx

from syncplaylists.integrations.platforms.soundcloud.client_id import ClientIdProvider
from syncplaylists.integrations.platforms.soundcloud.factory import (
    SoundCloudApiFactory,
    SoundCloudGatewayBuilder,
    SoundCloudLimits,
)
from syncplaylists.integrations.platforms.soundcloud.gateway import SoundCloudGateway
from syncplaylists.integrations.platforms.soundcloud.tokens import TokenEndpoint
from syncplaylists.shared_kernel.application.ports import (
    AccountAccess,
    CredentialsRenewal,
    PlatformCredentials,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport

API = "https://api-v2.soundcloud.com"
SITE = "https://soundcloud.com/"
BUNDLE_1 = "https://a-v2.sndcdn.com/assets/0-aaaa.js"
BUNDLE_2 = "https://a-v2.sndcdn.com/assets/55-bbbb.js"
CLIENT_ID = "A" * 32
FRESH_CLIENT_ID = "B" * 32
UID = "900001"
TOKEN = "2-000001-900001-test"
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "soundcloud"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def site_html() -> str:
    # Как на настоящей странице: трекер с чужого хоста (его не качаем) и бандлы.
    return (
        '<html><head><script src="https://dwt.soundcloud.com/tags.js"></script>'
        f'<script crossorigin src="{BUNDLE_1}"></script>'
        f'<script crossorigin src="{BUNDLE_2}"></script></head></html>'
    )


def bundle_with(client_id: str) -> str:
    return f'var x={{env:"production",client_id:"{client_id}",app_version:"1"}};'


def mock_site(router: respx.MockRouter, client_id: str = CLIENT_ID) -> None:
    router.get(SITE).respond(200, text=site_html())
    router.get(BUNDLE_1).respond(200, text="var nothing=1;")
    router.get(BUNDLE_2).respond(200, text=bundle_with(client_id))


class CountingLimiter:
    def __init__(self) -> None:
        self.acquired = 0
        self.penalties: list[float] = []
        self.counted = 0

    async def acquire(self, platform: Platform, account_id: UUID) -> None:
        self.acquired += 1

    async def penalize(self, platform: Platform, account_id: UUID, seconds: float) -> None:
        self.penalties.append(seconds)

    async def count_request(self, platform: Platform) -> None:
        self.counted += 1

    async def recent_requests(
        self, platform: Platform, account_id: UUID | None, minutes: int
    ) -> int:
        if account_id is None:
            return {10: 800, 60: 3000}[minutes]
        return {10: 40, 60: 300}[minutes]


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.sets = 0

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self.sets += 1
        self.data[key] = value


class MemoryRefresher:
    """CredentialsRefresher без БД: «строка аккаунта» — словарь."""

    def __init__(self) -> None:
        self.stored: dict[UUID, PlatformCredentials] = {}
        self.renewals = 0

    async def refresh(
        self, account_id: UUID, stale: PlatformCredentials, renew: CredentialsRenewal
    ) -> PlatformCredentials:
        current = self.stored.get(account_id, stale)
        if current.access_token != stale.access_token:
            return current
        self.renewals += 1
        fresh = await renew(current)
        self.stored[account_id] = fresh
        return fresh


@pytest.fixture
def router() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def limiter() -> CountingLimiter:
    return CountingLimiter()


@pytest.fixture
def cache() -> MemoryCache:
    data = MemoryCache()
    data.data["soundcloud:client_id"] = CLIENT_ID
    return data


@pytest.fixture
def refresher() -> MemoryRefresher:
    return MemoryRefresher()


@pytest.fixture
def client_ids(http: httpx.AsyncClient, cache: MemoryCache) -> ClientIdProvider:
    return ClientIdProvider(
        http, cache, ttl_seconds=3600, min_refresh_seconds=60, timeout_seconds=5
    )


@pytest.fixture
def apis(
    http: httpx.AsyncClient,
    client_ids: ClientIdProvider,
    limiter: CountingLimiter,
    refresher: MemoryRefresher,
) -> SoundCloudApiFactory:
    return SoundCloudApiFactory(
        http,
        client_ids,
        TokenEndpoint(http, timeout_seconds=5),
        timeout_seconds=5,
        limiter=limiter,
        refresher=refresher,
    )


def make_access(
    token: str = TOKEN,
    *,
    refresh_token: str | None = None,
    expires_at: datetime | None = None,
    transport: Transport = Transport.UNOFFICIAL,
    account_id: UUID | None = None,
) -> AccountAccess:
    return AccountAccess(
        account_id=account_id or uuid4(),
        user_id=uuid4(),
        platform=Platform.SOUNDCLOUD,
        transport=transport,
        external_user_id=UID,
        credentials=PlatformCredentials(
            access_token=token, refresh_token=refresh_token, expires_at=expires_at
        ),
    )


@pytest.fixture
def gateway(apis: SoundCloudApiFactory) -> SoundCloudGateway:
    limits = SoundCloudLimits(tracks_batch_size=50, likes_page_size=2, playlist_max_tracks=500)
    return SoundCloudGatewayBuilder(apis, limits)(make_access())


def jwt(claims: dict[str, Any]) -> str:
    import base64

    def part(data: dict[str, Any]) -> str:
        raw = json.dumps(data).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


def epoch(dt: datetime) -> int:
    return int(dt.replace(tzinfo=dt.tzinfo or UTC).timestamp())
