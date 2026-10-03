"""SoundCloudGateway против настоящего api-v2 на токене из .env (SOUNDCLOUD_LIVE_TOKEN).

Запуск: `uv run pytest -m live -s tests/live/soundcloud`.

Что меняется в аккаунте (с согласия владельца, план этапа 4b-2; лучше — отдельный
тестовый аккаунт):
- создаётся приватный сет «SyncPlaylists live-test <время>» — удаляется в finally;
- ставятся лайки трекам, которых в лайках ещё нет, — снимаются в finally.
  Уже лайкнутые треки тест не трогает (чтобы не снять настоящий лайк).
Если тест упал посреди — проверьте сеты и лайки вручную.
"""

import time
from typing import Any
from uuid import uuid4

import httpx
import pytest

from syncplaylists.integrations.platforms.soundcloud.api import V2Api
from syncplaylists.integrations.platforms.soundcloud.client_id import ClientIdProvider
from syncplaylists.integrations.platforms.soundcloud.factory import (
    SoundCloudApiFactory,
    SoundCloudGatewayBuilder,
    SoundCloudLimits,
    SoundCloudProfileFetcher,
)
from syncplaylists.integrations.platforms.soundcloud.gateway import SoundCloudGateway
from syncplaylists.integrations.platforms.soundcloud.ids import SoundCloudPlaylistId
from syncplaylists.integrations.platforms.soundcloud.tokens import (
    TokenEndpoint,
    token_client_id,
    token_expires_at,
)
from syncplaylists.shared_kernel.application.ports import (
    AccountAccess,
    CredentialsRenewal,
    PlatformCredentials,
)
from syncplaylists.shared_kernel.domain.errors import PlatformAuthError
from syncplaylists.shared_kernel.domain.search import InsertOrder, TrackCandidate, TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import (
    Platform,
    PlaylistRef,
    Transport,
)
from tests.live.conftest import LiveSettings

pytestmark = pytest.mark.live

_PROBE_QUERIES = (
    TrackQuery(title="Lucid Dreams", artist="Juice WRLD"),
    TrackQuery(title="Bohemian Rhapsody", artist="Queen"),
    TrackQuery(title="Smells Like Teen Spirit", artist="Nirvana"),
    TrackQuery(title="Billie Jean", artist="Michael Jackson"),
    TrackQuery(title="Wonderwall", artist="Oasis"),
)


class _NoopRefresher:
    """Live-тест не пишет в БД: продлённый токен просто живёт в памяти."""

    async def refresh(
        self, account_id: Any, stale: PlatformCredentials, renew: CredentialsRenewal
    ) -> PlatformCredentials:
        return await renew(stale)


class _Live:
    def __init__(self, gateway: SoundCloudGateway, api: V2Api, user_id: str) -> None:
        self.gateway = gateway
        self.api = api
        self.user_id = user_id


@pytest.fixture
def credentials(live_settings: LiveSettings) -> PlatformCredentials:
    if live_settings.soundcloud_live_token is None:
        pytest.skip("SOUNDCLOUD_LIVE_TOKEN не задан в .env")
    refresh = live_settings.soundcloud_live_refresh_token
    return PlatformCredentials(
        access_token=live_settings.soundcloud_live_token.get_secret_value(),
        refresh_token=refresh.get_secret_value() if refresh else None,
    )


@pytest.fixture
def apis(live_http: httpx.AsyncClient) -> SoundCloudApiFactory:
    client_ids = ClientIdProvider(
        live_http, None, ttl_seconds=3600, min_refresh_seconds=60, timeout_seconds=20
    )
    return SoundCloudApiFactory(
        live_http,
        client_ids,
        TokenEndpoint(live_http, timeout_seconds=20),
        timeout_seconds=20,
        refresher=_NoopRefresher(),
    )


@pytest.fixture
async def live(apis: SoundCloudApiFactory, credentials: PlatformCredentials) -> _Live:
    profile = await SoundCloudProfileFetcher(apis).fetch(credentials)
    access = AccountAccess(
        account_id=uuid4(),
        user_id=uuid4(),
        platform=Platform.SOUNDCLOUD,
        transport=Transport.UNOFFICIAL,
        external_user_id=profile.external_user_id,
        credentials=credentials,
    )
    api = apis.for_account(access)
    assert isinstance(api, V2Api)
    gateway = SoundCloudGatewayBuilder(apis, SoundCloudLimits(likes_page_size=50))(access)
    return _Live(gateway, api, profile.external_user_id)


def test_token_shape_is_reported(credentials: PlatformCredentials) -> None:
    # Срок и client_id из claims (если токен — JWT): от этого зависит, нужен ли
    # refresh_token для долгих переносов. Сам токен не печатаем.
    expires_at = token_expires_at(credentials.access_token)
    print(
        f"\nJWT: {expires_at is not None}, истекает: {expires_at}, "
        f"client_id в claims: {token_client_id(credentials.access_token) is not None}, "
        f"refresh_token задан: {credentials.refresh_token is not None}"
    )


async def test_profile_and_bad_token(apis: SoundCloudApiFactory, live: _Live) -> None:
    assert live.user_id.isdigit()
    with pytest.raises(PlatformAuthError):
        await SoundCloudProfileFetcher(apis).fetch(PlatformCredentials(access_token="2-0-0-bad"))


async def test_read_likes_first_page(live: _Live) -> None:
    liked: list[TrackCandidate] = []
    async for candidate in live.gateway.get_library():
        liked.append(candidate)
        if len(liked) >= 50:
            break
    print(f"\nлайков прочитано: {len(liked)}")
    assert all(c.ref.external_id.isdigit() for c in liked)


async def test_search_finds_official_upload(live: _Live) -> None:
    found = await live.gateway.search(_PROBE_QUERIES[0], limit=20)
    assert found
    assert any(c.rights_holder and c.isrc is not None for c in found)


async def test_stale_client_id_is_refreshed(apis: SoundCloudApiFactory, live: _Live) -> None:
    transport = live.api.transport
    provider = transport._client_ids
    assert provider is not None
    provider._value = "0" * 32
    me = await live.api.me()
    assert str(me["id"]) == live.user_id
    assert provider._value != "0" * 32


async def _new_probe_tracks(live: _Live, count: int) -> list[TrackCandidate]:
    liked = await live.api.liked_track_ids(live.user_id)
    probes: list[TrackCandidate] = []
    for query in _PROBE_QUERIES:
        for candidate in await live.gateway.search(query, limit=5):
            if int(candidate.ref.external_id) not in liked and candidate.restriction is None:
                probes.append(candidate)
                break
        if len(probes) == count:
            return probes
    pytest.skip("не нашлось треков, которых нет в лайках")


async def test_playlist_create_add_read_delete(live: _Live) -> None:
    probes = await _new_probe_tracks(live, 3)
    ref = await live.gateway.create_playlist(
        f"SyncPlaylists live-test {int(time.time())}", "временный, удалится"
    )
    playlist_id = SoundCloudPlaylistId.parse(ref.external_id).playlist_id
    assert playlist_id is not None
    try:
        first = await live.gateway.add_tracks(ref, [p.ref for p in probes[:2]])
        assert first.failed == ()
        # Повтор с пересечением — дубли не добавляются.
        second = await live.gateway.add_tracks(ref, [p.ref for p in probes[1:]])
        assert second.failed == ()
        snapshot = await live.gateway.get_playlist(ref)
        assert [t.ref for t in snapshot.tracks] == [p.ref for p in probes]
        info = await live.gateway.playlist_info(ref)
        assert info.owner_external_id == live.user_id
    finally:
        await live.api.transport.request("DELETE", f"/playlists/{playlist_id}")


async def test_like_order_and_cleanup(live: _Live) -> None:
    """Порядок лайков: два лайка подряд — второй должен оказаться выше (TOP)."""
    probes = await _new_probe_tracks(live, 2)
    try:
        result = await live.gateway.add_to_library([p.ref for p in probes])
        assert result.failed == ()
        top: list[str] = []
        async for candidate in live.gateway.get_library():
            top.append(candidate.ref.external_id)
            if len(top) >= 2:
                break
        print(f"\nсверху лайков: {top}, ставили: {[p.ref.external_id for p in probes]}")
        assert live.gateway.library_insert_order() is InsertOrder.TOP
        assert top == [probes[1].ref.external_id, probes[0].ref.external_id]
    finally:
        for probe in probes:
            await live.api.transport.request(
                "DELETE", f"/users/{live.user_id}/track_likes/{probe.ref.external_id}"
            )


async def test_private_set_by_link(live: _Live, live_settings: LiveSettings) -> None:
    """Приватный сет по ссылке с s-…: создаём свой приватный, читаем по его ссылке."""
    ref = await live.gateway.create_playlist(f"SyncPlaylists live-private {int(time.time())}", None)
    playlist_id = SoundCloudPlaylistId.parse(ref.external_id)
    assert playlist_id.playlist_id is not None
    try:
        data = await live.api.playlist(playlist_id.playlist_id, playlist_id.secret_token)
        url = str(data["permalink_url"])
        if data.get("secret_token") and "/s-" not in url:
            url = f"{url}/{data['secret_token']}"
        path = url.removeprefix("https://soundcloud.com/")
        snapshot = await live.gateway.get_playlist(PlaylistRef(Platform.SOUNDCLOUD, path))
        assert snapshot.title.startswith("SyncPlaylists live-private")
    finally:
        await live.api.transport.request("DELETE", f"/playlists/{playlist_id.playlist_id}")
