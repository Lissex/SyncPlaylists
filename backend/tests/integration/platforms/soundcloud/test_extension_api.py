"""SoundCloud через расширение (4c-3): тот же SoundCloudGateway поверх
ExtensionSoundCloudApi. Публичное сервер читает анонимно (respx), личное и запись — через
канал расширения (ScriptedChannel). Без сети и без Docker."""

from typing import Any
from uuid import uuid4

import pytest
import respx

from syncplaylists.integrations.platforms.registry import PlatformGatewayFactory
from syncplaylists.integrations.platforms.soundcloud.factory import (
    SoundCloudApiFactory,
    SoundCloudExtensionGatewayBuilder,
    SoundCloudLimits,
)
from syncplaylists.integrations.platforms.soundcloud.gateway import SoundCloudGateway
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.errors import (
    ExtensionUnavailableError,
    ExtensionUnavailableReason,
    PlatformUnavailableError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.search import TrackQuery, TrackRestriction
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    ExternalTrackRef,
    Platform,
    PlaylistRef,
    Transport,
)
from tests.fakes.extension import ScriptedChannel
from tests.integration.platforms.soundcloud.conftest import API, UID, fixture, make_access


def _access() -> AccountAccess:
    return AccountAccess(
        account_id=uuid4(),
        user_id=uuid4(),
        platform=Platform.SOUNDCLOUD,
        transport=Transport.EXTENSION,
        external_user_id=UID,
        credentials=None,
    )


def _track(track_id: int, title: str = "Starboy", **extra: Any) -> dict[str, Any]:
    return {
        "id": track_id,
        "kind": "track",
        "title": title,
        "duration": 230000,
        "full_duration": 230000,
        "policy": "ALLOW",
        "user": {"id": 77, "username": "The Weeknd", "verified": True},
        **extra,
    }


def _ref(external_id: str) -> ExternalTrackRef:
    return ExternalTrackRef(Platform.SOUNDCLOUD, external_id)


@pytest.fixture
def channel() -> ScriptedChannel:
    return ScriptedChannel()


@pytest.fixture
def gateway(apis: SoundCloudApiFactory, channel: ScriptedChannel) -> SoundCloudGateway:
    limits = SoundCloudLimits(tracks_batch_size=50, likes_page_size=2, playlist_max_tracks=500)
    return SoundCloudExtensionGatewayBuilder(apis, channel, limits)(_access())


# --- публичное — на сервере, анонимно ------------------------------------------------


async def test_search_is_read_by_server_anonymously(
    router: respx.MockRouter, gateway: SoundCloudGateway, channel: ScriptedChannel
) -> None:
    route = router.get(f"{API}/search/tracks").respond(200, json=fixture("search_tracks"))

    found = await gateway.search(TrackQuery(title="Lucid Dreams", artist="Juice WRLD"))

    assert found
    request = route.calls[0].request
    assert "authorization" not in request.headers  # токена у сервера нет
    assert request.url.params["client_id"]
    assert channel.calls == []


async def test_someone_elses_likes_are_read_by_server(
    router: respx.MockRouter, gateway: SoundCloudGateway, channel: ScriptedChannel
) -> None:
    router.get(f"{API}/resolve").respond(200, json=fixture("user_artist"))
    user_id = fixture("user_artist")["id"]
    router.get(f"{API}/users/{user_id}/track_likes").respond(200, json=fixture("likes_page2"))

    snapshot = await gateway.get_playlist(PlaylistRef(Platform.SOUNDCLOUD, "some-artist/likes"))

    assert snapshot.tracks
    assert channel.calls == []


# --- личное — в браузере -------------------------------------------------------------


async def test_library_is_read_page_by_page_in_browser(
    gateway: SoundCloudGateway, channel: ScriptedChannel
) -> None:
    channel.reply(
        "liked_tracks_page",
        {
            "tracks": [
                _track(1, publisher_metadata={"artist": "The Weeknd", "isrc": "USUG11600976"})
            ],
            "next_cursor": "offset=2&limit=2",
        },
        {"tracks": [_track(2, "Preview", policy="SNIP", duration=30000)], "next_cursor": None},
    )

    tracks = [t async for t in gateway.get_library()]

    assert [t.ref.external_id for t in tracks] == ["1", "2"]
    assert tracks[0].isrc == ISRC("USUG11600976")
    assert tracks[1].restriction is TrackRestriction.PREVIEW_ONLY
    assert tracks[1].duration is not None
    assert tracks[1].duration.milliseconds == 230000
    assert [c.args for c in channel.calls] == [
        {"cursor": None, "limit": 2},
        {"cursor": "offset=2&limit=2", "limit": 2},
    ]
    assert all(c.external_user_id == UID for c in channel.calls)


async def test_own_likes_link_uses_whoami(
    gateway: SoundCloudGateway, channel: ScriptedChannel
) -> None:
    channel.reply("whoami", {"id": int(UID), "username": "Test Listener", "likes_count": 1})
    channel.reply("liked_tracks_page", {"tracks": [_track(1)], "next_cursor": None})

    snapshot = await gateway.get_playlist(PlaylistRef(Platform.SOUNDCLOUD, "you/likes"))

    assert snapshot.title == "Лайки Test Listener"
    assert [t.ref.external_id for t in snapshot.tracks] == ["1"]


async def test_response_with_extra_field_is_rejected(
    gateway: SoundCloudGateway, channel: ScriptedChannel
) -> None:
    # Проекция строгая: случайно попавшее поле (токен из страницы) — ответ не по схеме.
    channel.reply(
        "liked_tracks_page",
        {"tracks": [{**_track(1), "oauth_token": "2-secret"}], "next_cursor": None},
    )

    with pytest.raises(PlatformUnavailableError, match="не по схеме"):
        [t async for t in gateway.get_library()]


async def test_extension_unavailable_is_propagated(
    gateway: SoundCloudGateway, channel: ScriptedChannel
) -> None:
    channel.reply(
        "liked_tracks_page",
        ExtensionUnavailableError(Platform.SOUNDCLOUD, ExtensionUnavailableReason.CAPTCHA),
    )

    with pytest.raises(ExtensionUnavailableError) as caught:
        [t async for t in gateway.get_library()]
    assert caught.value.reason is ExtensionUnavailableReason.CAPTCHA


# --- запись — в браузере -------------------------------------------------------------


async def test_create_playlist_carries_idempotency_key(
    gateway: SoundCloudGateway, channel: ScriptedChannel
) -> None:
    channel.reply("create_playlist", {"id": 5100, "secret": "s-NeW999"})

    ref = await gateway.create_playlist("Перенос", None, request_id="r-1")

    assert ref == PlaylistRef(Platform.SOUNDCLOUD, "5100:s-NeW999")
    call = channel.calls[0]
    assert call.idempotency_key == "create_playlist:r-1"
    assert call.args == {"title": "Перенос", "description": None}


async def test_add_tracks_to_created_set_reads_it_in_browser_and_replaces_list(
    router: respx.MockRouter, gateway: SoundCloudGateway, channel: ScriptedChannel
) -> None:
    channel.reply(
        "playlist",
        {
            "id": 5100,
            "kind": "playlist",
            "title": "Перенос",
            "user_id": int(UID),
            "secret": "s-NeW999",
            "tracks": [{"id": 1, "kind": "track", "policy": "ALLOW"}],
        },
    )
    channel.reply("set_playlist_tracks", {})
    server = router.route(host="api-v2.soundcloud.com")

    result = await gateway.add_tracks(
        PlaylistRef(Platform.SOUNDCLOUD, "5100:s-NeW999"), [_ref("1"), _ref("2"), _ref("3")]
    )

    assert result.added == (_ref("1"), _ref("2"), _ref("3"))  # 1 уже был — не задваиваем
    assert [c.operation for c in channel.calls] == ["playlist", "set_playlist_tracks"]
    assert channel.calls[0].args == {"playlist_id": 5100, "secret": "s-NeW999"}
    assert channel.calls[1].args == {
        "playlist_id": 5100,
        "secret": "s-NeW999",
        "track_ids": [1, 2, 3],
    }
    assert not server.called  # приватный сет сервер не читает


async def test_set_of_another_account_is_not_writable(
    gateway: SoundCloudGateway, channel: ScriptedChannel
) -> None:
    channel.reply("playlist", {"id": 5100, "kind": "playlist", "user_id": 1, "secret": "s-x"})

    with pytest.raises(PlaylistNotWritableError):
        await gateway.add_tracks(PlaylistRef(Platform.SOUNDCLOUD, "5100:s-x"), [_ref("2")])


async def test_likes_skip_liked_and_count_refused_as_failed(
    gateway: SoundCloudGateway, channel: ScriptedChannel
) -> None:
    channel.reply(
        "liked_track_ids_page",
        {"ids": [1], "next_cursor": "cursor=abc"},
        {"ids": [5], "next_cursor": None},
    )
    channel.reply(
        "like",
        {},
        PlaylistNotWritableError(Platform.SOUNDCLOUD, "расширение: not_writable"),
    )

    result = await gateway.add_to_library([_ref("1"), _ref("2"), _ref("3"), _ref("5")])

    assert set(result.added) == {_ref("1"), _ref("2"), _ref("5")}
    assert result.failed == (_ref("3"),)
    likes = [c for c in channel.calls if c.operation == "like"]
    assert [c.args for c in likes] == [{"track_id": 2}, {"track_id": 3}]


# --- транспорт только для чтения -----------------------------------------------------


def test_soundcloud_by_token_is_read_only() -> None:
    factory = PlatformGatewayFactory(
        {}, {}, {Platform.SOUNDCLOUD: frozenset({Transport.UNOFFICIAL})}
    )

    assert not factory.can_write(make_access())
    assert factory.can_write(_access())
