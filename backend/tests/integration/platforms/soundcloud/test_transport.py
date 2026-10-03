"""Транспорт SoundCloud: client_id, 401 токена ≠ 401 client_id, продление токена,
429/антибот — respx, без сети."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from syncplaylists.integrations.platforms.soundcloud.client_id import ClientIdProvider
from syncplaylists.integrations.platforms.soundcloud.factory import (
    SoundCloudApiFactory,
    SoundCloudProfileFetcher,
)
from syncplaylists.integrations.platforms.soundcloud.tokens import TOKEN_URL
from syncplaylists.shared_kernel.application.ports import PlatformCredentials
from syncplaylists.shared_kernel.domain.errors import (
    PlatformAuthError,
    PlatformRateLimitedError,
    PlatformUnavailableError,
)
from tests.integration.platforms.soundcloud.conftest import (
    API,
    BUNDLE_1,
    CLIENT_ID,
    FRESH_CLIENT_ID,
    SITE,
    TOKEN,
    CountingLimiter,
    MemoryCache,
    MemoryRefresher,
    epoch,
    fixture,
    jwt,
    make_access,
    mock_site,
)


def _client_id(request: httpx.Request) -> str | None:
    return request.url.params.get("client_id")


def _auth(request: httpx.Request) -> str | None:
    return request.headers.get("authorization")


# --- client_id -------------------------------------------------------------------------


async def test_client_id_scraped_once_and_cached(
    router: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    mock_site(router)
    cache = MemoryCache()
    provider = ClientIdProvider(
        http, cache, ttl_seconds=3600, min_refresh_seconds=60, timeout_seconds=5
    )

    assert await provider.get() == CLIENT_ID
    assert await provider.get() == CLIENT_ID
    assert router.routes[0].call_count == 1  # сайт скачан один раз
    assert cache.data["soundcloud:client_id"] == CLIENT_ID
    # Второй процесс берёт из Redis, не трогая сайт.
    other = ClientIdProvider(
        http, cache, ttl_seconds=3600, min_refresh_seconds=60, timeout_seconds=5
    )
    assert await other.get() == CLIENT_ID
    assert router.routes[0].call_count == 1


async def test_parallel_refresh_scrapes_once(
    router: respx.MockRouter, client_ids: ClientIdProvider
) -> None:
    mock_site(router, FRESH_CLIENT_ID)
    assert await client_ids.get() == CLIENT_ID

    results = await asyncio.gather(*(client_ids.refresh(CLIENT_ID) for _ in range(5)))

    assert results == [FRESH_CLIENT_ID] * 5
    assert router.routes[0].call_count == 1


async def test_refresh_uses_value_updated_by_another_worker(
    router: respx.MockRouter, client_ids: ClientIdProvider, cache: MemoryCache
) -> None:
    mock_site(router, "C" * 32)
    await client_ids.get()
    cache.data["soundcloud:client_id"] = FRESH_CLIENT_ID  # другой воркер уже обновил

    assert await client_ids.refresh(CLIENT_ID) == FRESH_CLIENT_ID
    assert router.routes[0].call_count == 0


async def test_refresh_not_more_often_than_min_interval(
    router: respx.MockRouter, client_ids: ClientIdProvider
) -> None:
    mock_site(router, CLIENT_ID)  # сайт отдаёт тот же client_id
    await client_ids.get()

    assert await client_ids.refresh(CLIENT_ID) is None  # скачали — тот же: дело не в нём
    assert await client_ids.refresh(CLIENT_ID) is None  # второй раз сайт не качаем
    assert router.routes[0].call_count == 1


async def test_client_id_not_found_is_temporary(
    router: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    router.get(SITE).respond(200, text=f'<script src="{BUNDLE_1}"></script>')
    router.get(BUNDLE_1).respond(200, text="var nothing=1;")
    provider = ClientIdProvider(
        http, None, ttl_seconds=3600, min_refresh_seconds=60, timeout_seconds=5
    )
    with pytest.raises(PlatformUnavailableError, match="client_id не найден"):
        await provider.get()


async def test_override_is_used_without_scraping(
    router: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    provider = ClientIdProvider(
        http,
        None,
        ttl_seconds=3600,
        min_refresh_seconds=60,
        timeout_seconds=5,
        override=FRESH_CLIENT_ID,
    )
    assert await provider.get() == FRESH_CLIENT_ID
    assert await provider.refresh(FRESH_CLIENT_ID) is None
    assert not router.calls


# --- 401: client_id или токен ----------------------------------------------------------


async def test_stale_client_id_is_refreshed_and_request_repeated(
    router: respx.MockRouter, apis: SoundCloudApiFactory, limiter: CountingLimiter
) -> None:
    mock_site(router, FRESH_CLIENT_ID)

    def me(request: httpx.Request) -> httpx.Response:
        if _client_id(request) == FRESH_CLIENT_ID:
            return httpx.Response(200, json=fixture("me"))
        return httpx.Response(401)

    route = router.get(f"{API}/me").mock(side_effect=me)

    data = await apis.for_account(make_access()).me()

    assert data["id"] == 900001
    assert [_client_id(call.request) for call in route.calls] == [CLIENT_ID, FRESH_CLIENT_ID]
    assert all(_auth(call.request) == f"OAuth {TOKEN}" for call in route.calls)
    assert limiter.acquired == 2  # повтор тоже через token bucket


async def test_401_with_fresh_client_id_is_token_error(
    router: respx.MockRouter, apis: SoundCloudApiFactory
) -> None:
    mock_site(router, FRESH_CLIENT_ID)
    route = router.get(f"{API}/me").respond(401)

    with pytest.raises(PlatformAuthError):
        await apis.for_account(make_access()).me()
    assert [_client_id(call.request) for call in route.calls] == [CLIENT_ID, FRESH_CLIENT_ID]


async def test_profile_fetcher_rejects_bad_token(
    router: respx.MockRouter, apis: SoundCloudApiFactory
) -> None:
    mock_site(router, CLIENT_ID)  # client_id не менялся — 401 относится к токену
    router.get(f"{API}/me").respond(401)

    with pytest.raises(PlatformAuthError):
        await SoundCloudProfileFetcher(apis).fetch(PlatformCredentials(access_token="bad"))


async def test_profile_fetcher_returns_profile(
    router: respx.MockRouter, apis: SoundCloudApiFactory
) -> None:
    router.get(f"{API}/me").respond(200, json=fixture("me"))
    profile = await SoundCloudProfileFetcher(apis).fetch(PlatformCredentials(access_token=TOKEN))
    assert (profile.external_user_id, profile.display_name) == ("900001", "Test Listener")


# --- продление токена (JWT + refresh_token) --------------------------------------------


async def test_expiring_token_is_refreshed_before_request(
    router: respx.MockRouter, apis: SoundCloudApiFactory, refresher: MemoryRefresher
) -> None:
    soon = datetime.now(UTC) + timedelta(seconds=10)
    old = jwt({"exp": epoch(soon), "client_id": "web-client"})
    new = jwt({"exp": epoch(soon + timedelta(hours=1)), "client_id": "web-client"})
    token_route = router.post(TOKEN_URL).respond(
        200, json={"access_token": new, "refresh_token": "rt-2", "expires_in": 3600}
    )
    me_route = router.get(f"{API}/me").respond(200, json=fixture("me"))
    access = make_access(old, refresh_token="rt-1")

    await apis.for_account(access).me()

    form = dict(httpx.QueryParams(token_route.calls[0].request.content.decode()))
    assert form == {
        "grant_type": "refresh_token",
        "refresh_token": "rt-1",
        "client_id": "web-client",  # из claims токена — как делает сам сайт
    }
    assert _auth(me_route.calls[0].request) == f"OAuth {new}"
    stored = refresher.stored[access.account_id]
    assert (stored.access_token, stored.refresh_token) == (new, "rt-2")


async def test_401_triggers_token_refresh_and_retry(
    router: respx.MockRouter, apis: SoundCloudApiFactory
) -> None:
    mock_site(router, CLIENT_ID)  # client_id в порядке
    router.post(TOKEN_URL).respond(200, json={"access_token": "new-token", "expires_in": 3600})

    def me(request: httpx.Request) -> httpx.Response:
        if _auth(request) == "OAuth new-token":
            return httpx.Response(200, json=fixture("me"))
        return httpx.Response(401)

    route = router.get(f"{API}/me").mock(side_effect=me)

    await apis.for_account(make_access("old-token", refresh_token="rt-1")).me()
    assert _auth(route.calls[-1].request) == "OAuth new-token"


async def test_revoked_refresh_token_is_auth_error(
    router: respx.MockRouter, apis: SoundCloudApiFactory
) -> None:
    expired = jwt({"exp": epoch(datetime.now(UTC) - timedelta(hours=1)), "client_id": "w"})
    router.post(TOKEN_URL).respond(400, json={"error": "invalid_grant"})

    with pytest.raises(PlatformAuthError, match="invalid_grant"):
        await apis.for_account(make_access(expired, refresh_token="rt-old")).me()


async def test_without_refresh_token_expired_jwt_is_sent_as_is(
    router: respx.MockRouter, apis: SoundCloudApiFactory
) -> None:
    expired = jwt({"exp": epoch(datetime.now(UTC) - timedelta(hours=1))})
    route = router.get(f"{API}/me").respond(200, json=fixture("me"))
    await apis.for_account(make_access(expired)).me()
    assert _auth(route.calls[0].request) == f"OAuth {expired}"


# --- 429, антибот, 5xx -----------------------------------------------------------------


async def test_rate_limited_penalizes_account_and_logs_bucket(
    router: respx.MockRouter,
    apis: SoundCloudApiFactory,
    limiter: CountingLimiter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    router.get(f"{API}/me").respond(429, json=fixture("error_rate_limited"))

    with caplog.at_level("WARNING"), pytest.raises(PlatformRateLimitedError) as exc_info:
        await apis.for_account(make_access()).me()

    # reset_time из тела (в прошлом относительно «сейчас» — значит минимум 1 с).
    assert exc_info.value.retry_after_seconds >= 1.0
    assert limiter.penalties == [exc_info.value.retry_after_seconds]
    assert "аккаунт 40/300, сервер (IP) 800/3000" in caplog.text
    assert "bucket by-client" in caplog.text
    assert TOKEN not in caplog.text


async def test_retry_after_header_wins(
    router: respx.MockRouter, apis: SoundCloudApiFactory
) -> None:
    router.get(f"{API}/me").respond(429, headers={"Retry-After": "120"})
    with pytest.raises(PlatformRateLimitedError) as exc_info:
        await apis.for_account(make_access()).me()
    assert exc_info.value.retry_after_seconds == 120.0


async def test_bot_challenge_is_temporary_and_keeps_client_id(
    router: respx.MockRouter, apis: SoundCloudApiFactory, client_ids: ClientIdProvider
) -> None:
    site = router.get(SITE).respond(200, text="")
    router.get(f"{API}/me").respond(
        403,
        headers={"content-type": "text/html", "x-datadome": "protected"},
        text='<html><script src="https://ct.captcha-delivery.com/c.js"></script></html>',
    )
    with pytest.raises(PlatformUnavailableError, match="антибот"):
        await apis.for_account(make_access()).me()
    assert site.call_count == 0
    assert await client_ids.get() == CLIENT_ID


async def test_server_error_is_temporary(
    router: respx.MockRouter, apis: SoundCloudApiFactory
) -> None:
    router.get(f"{API}/me").respond(503)
    with pytest.raises(PlatformUnavailableError):
        await apis.for_account(make_access()).me()


async def test_network_error_is_temporary(
    router: respx.MockRouter, apis: SoundCloudApiFactory
) -> None:
    router.get(f"{API}/me").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(PlatformUnavailableError):
        await apis.for_account(make_access()).me()
