"""SoundCloudGateway на ответах формата api-v2 (tests/fixtures/soundcloud, respx) — без
сети и без Docker. Сверить форму с настоящими ответами: tests/tools/record_soundcloud.py."""

import json
from typing import Any

import httpx
import pytest
import respx

from syncplaylists.integrations.platforms.soundcloud.factory import (
    SoundCloudApiFactory,
    SoundCloudGatewayBuilder,
    SoundCloudLimits,
)
from syncplaylists.integrations.platforms.soundcloud.gateway import SoundCloudGateway
from syncplaylists.shared_kernel.domain.errors import (
    PlaylistNotFoundError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.search import InsertOrder, TrackQuery, TrackRestriction
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    Platform,
    PlaylistRef,
)
from tests.integration.platforms.soundcloud.conftest import API, UID, fixture, make_access

_SET_PATH = "test-listener/sets/road-trip/s-AbC123"
_SET_URL = f"https://soundcloud.com/{_SET_PATH}"


def _ref(external_id: str) -> ExternalTrackRef:
    return ExternalTrackRef(Platform.SOUNDCLOUD, external_id)


def _body(request: httpx.Request) -> Any:
    return json.loads(request.content)


def _resolve(router: respx.MockRouter, url: str, body: Any, status: int = 200) -> respx.Route:
    return router.get(f"{API}/resolve", params={"url": url}).respond(status, json=body)


# --- чтение ----------------------------------------------------------------------------


async def test_search_skips_blocked_tracks(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    route = router.get(f"{API}/search/tracks").respond(200, json=fixture("search_tracks"))

    found = await gateway.search(TrackQuery(title="Lucid Dreams", artist="Juice WRLD"), limit=10)

    assert [c.ref.external_id for c in found] == ["1002", "1001", "1004"]  # 1003 — BLOCK
    params = route.calls[0].request.url.params
    assert (params["q"], params["limit"]) == ("Juice WRLD Lucid Dreams", "10")
    assert found[2].restriction is TrackRestriction.PREVIEW_ONLY


async def test_private_set_by_link_loads_stub_tracks_in_order(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    _resolve(router, _SET_URL, fixture("playlist"))
    tracks = router.get(f"{API}/tracks").respond(200, json=fixture("tracks_batch"))

    snapshot = await gateway.get_playlist(PlaylistRef(Platform.SOUNDCLOUD, _SET_PATH))

    assert snapshot.title == "Road trip"
    assert snapshot.description == "летние треки"
    # Порядок сета; 1099 удалён (нет в ответе /tracks) — пропущен.
    assert [t.ref.external_id for t in snapshot.tracks] == ["1001", "1002", "1004", "1005"]
    params = tracks.calls[0].request.url.params
    assert params["ids"] == "1004,1099,1005"
    # Приватные треки приватного сета — только с его секретом.
    assert (params["playlistId"], params["playlistSecretToken"]) == ("5001", "s-AbC123")


async def test_stub_tracks_are_loaded_in_batches(
    router: respx.MockRouter, apis: SoundCloudApiFactory
) -> None:
    gateway = SoundCloudGatewayBuilder(apis, SoundCloudLimits(tracks_batch_size=2))(make_access())
    _resolve(router, _SET_URL, fixture("playlist"))
    tracks = router.get(f"{API}/tracks").respond(200, json=fixture("tracks_batch"))

    await gateway.get_playlist(PlaylistRef(Platform.SOUNDCLOUD, _SET_PATH))

    assert [c.request.url.params["ids"] for c in tracks.calls] == ["1004,1099", "1005"]


async def test_playlist_info_is_one_request(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    route = _resolve(router, _SET_URL, fixture("playlist"))

    info = await gateway.playlist_info(PlaylistRef(Platform.SOUNDCLOUD, _SET_PATH))

    assert (info.title, info.owner_external_id, info.track_count) == ("Road trip", UID, 5)
    assert route.call_count == 1
    assert len(router.calls) == 1


async def test_created_playlist_is_read_by_id_with_secret(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    route = router.get(f"{API}/playlists/5001").respond(200, json=fixture("playlist"))
    router.get(f"{API}/tracks").respond(200, json=fixture("tracks_batch"))

    await gateway.get_playlist(PlaylistRef(Platform.SOUNDCLOUD, "5001:s-AbC123"))

    assert route.calls[0].request.url.params["secret_token"] == "s-AbC123"


@pytest.mark.parametrize("status", [403, 404])
async def test_missing_or_closed_set_is_not_found(
    router: respx.MockRouter, gateway: SoundCloudGateway, status: int
) -> None:
    _resolve(router, _SET_URL, {}, status=status)
    with pytest.raises(PlaylistNotFoundError):
        await gateway.get_playlist(PlaylistRef(Platform.SOUNDCLOUD, _SET_PATH))


async def test_link_to_user_instead_of_set_is_not_found(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    _resolve(router, _SET_URL, fixture("user_artist"))
    with pytest.raises(PlaylistNotFoundError):
        await gateway.playlist_info(PlaylistRef(Platform.SOUNDCLOUD, _SET_PATH))


async def test_library_pages_through_likes(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    first = router.get(f"{API}/users/{UID}/track_likes", params={"linked_partitioning": "1"})
    first.respond(200, json=fixture("likes_page1"))
    router.get(f"{API}/users/{UID}/track_likes", params={"offset": "2026-09-30T10:00:00Z"}).respond(
        200, json=fixture("likes_page2")
    )

    liked = [c.ref.external_id async for c in gateway.get_library()]

    # Свежие сверху; лайк без трека (удалён) пропущен.
    assert liked == ["1001", "1005", "1004"]
    assert first.calls[0].request.url.params["limit"] == "2"


async def test_someone_elses_likes_are_a_readable_source(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    _resolve(router, "https://soundcloud.com/juice-wrld", fixture("user_artist"))
    router.get(f"{API}/users/700001/track_likes").respond(
        200, json=dict(fixture("likes_page2"), next_href=None)
    )

    snapshot = await gateway.get_playlist(PlaylistRef(Platform.SOUNDCLOUD, "juice-wrld/likes"))

    assert snapshot.title == "Лайки Juice WRLD"
    assert [t.ref.external_id for t in snapshot.tracks] == ["1004"]


async def test_is_own_library(router: respx.MockRouter, gateway: SoundCloudGateway) -> None:
    router.get(f"{API}/me").respond(200, json=fixture("me"))
    assert await gateway.is_own_library(PlaylistRef(Platform.SOUNDCLOUD, "Test-Listener/likes"))
    assert not await gateway.is_own_library(PlaylistRef(Platform.SOUNDCLOUD, "other/likes"))
    assert not await gateway.is_own_library(PlaylistRef(Platform.SOUNDCLOUD, _SET_PATH))


def test_orders(gateway: SoundCloudGateway) -> None:
    assert gateway.library_insert_order() is InsertOrder.TOP
    assert gateway.playlist_capacity() == 500


# --- запись ----------------------------------------------------------------------------


async def test_create_private_playlist(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    route = router.post(f"{API}/playlists").respond(201, json=fixture("playlist_created"))

    ref = await gateway.create_playlist("Перенос", "из Яндекса")

    assert ref == PlaylistRef(Platform.SOUNDCLOUD, "5100:s-NeW999")
    assert _body(route.calls[0].request) == {
        "playlist": {
            "title": "Перенос",
            "sharing": "private",
            "tracks": [],
            "description": "из Яндекса",
        }
    }


async def test_add_tracks_appends_full_list_without_duplicates(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    router.get(f"{API}/playlists/5001").respond(200, json=fixture("playlist"))
    put = router.put(f"{API}/playlists/5001").respond(200, json={})

    result = await gateway.add_tracks(
        PlaylistRef(Platform.SOUNDCLOUD, "5001:s-AbC123"),
        [_ref("1001"), _ref("2001"), _ref("2002"), _ref("2001"), _ref("abc")],
    )

    # 1001 уже в сете, второй 2001 — дубль запроса: «добавлены»; abc — чужой формат.
    assert result.added == (_ref("1001"), _ref("2001"), _ref("2001"), _ref("2002"))
    assert result.failed == (_ref("abc"),)
    # Список заменяется целиком: старые треки (включая заглушки) + новые в конец.
    assert _body(put.calls[0].request) == {
        "playlist": {"tracks": [1001, 1002, 1004, 1099, 1005, 2001, 2002]}
    }
    assert put.calls[0].request.url.params["secret_token"] == "s-AbC123"


async def test_add_tracks_over_limit_puts_rest_to_failed(
    router: respx.MockRouter, apis: SoundCloudApiFactory, caplog: pytest.LogCaptureFixture
) -> None:
    gateway = SoundCloudGatewayBuilder(apis, SoundCloudLimits(playlist_max_tracks=6))(make_access())
    router.get(f"{API}/playlists/5001").respond(200, json=fixture("playlist"))  # 5 треков
    put = router.put(f"{API}/playlists/5001").respond(200, json={})

    with caplog.at_level("WARNING"):
        result = await gateway.add_tracks(
            PlaylistRef(Platform.SOUNDCLOUD, "5001"), [_ref("2001"), _ref("2002")]
        )

    assert result.added == (_ref("2001"),)
    assert result.failed == (_ref("2002"),)
    assert _body(put.calls[0].request)["playlist"]["tracks"][-1] == 2001
    assert "не влезло 1" in caplog.text


async def test_nothing_new_means_no_write(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    router.get(f"{API}/playlists/5001").respond(200, json=fixture("playlist"))
    put = router.put(f"{API}/playlists/5001")

    result = await gateway.add_tracks(PlaylistRef(Platform.SOUNDCLOUD, "5001"), [_ref("1002")])

    assert result.added == (_ref("1002"),)
    assert put.call_count == 0


async def test_foreign_set_is_not_writable(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    router.get(f"{API}/playlists/5002").respond(200, json=fixture("playlist_foreign"))
    with pytest.raises(PlaylistNotWritableError):
        await gateway.add_tracks(PlaylistRef(Platform.SOUNDCLOUD, "5002"), [_ref("2001")])


async def test_forbidden_write_is_not_writable(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    router.get(f"{API}/playlists/5001").respond(200, json=fixture("playlist"))
    router.put(f"{API}/playlists/5001").respond(403)
    with pytest.raises(PlaylistNotWritableError):
        await gateway.add_tracks(PlaylistRef(Platform.SOUNDCLOUD, "5001"), [_ref("2001")])


async def test_likes_are_put_one_by_one_skipping_liked(
    router: respx.MockRouter, gateway: SoundCloudGateway
) -> None:
    router.get(f"{API}/me/track_likes/ids").respond(200, json=fixture("like_ids"))
    like = router.put(path__regex=rf"^/users/{UID}/track_likes/\d+$").mock(
        side_effect=lambda request: httpx.Response(
            400 if request.url.path.endswith("/3003") else 200
        )
    )

    result = await gateway.add_to_library([_ref("2001"), _ref("1001"), _ref("2002"), _ref("3003")])

    assert [c.request.url.path.rsplit("/", 1)[1] for c in like.calls] == ["2001", "2002", "3003"]
    assert result.added == (_ref("2001"), _ref("1001"), _ref("2002"))
    assert result.failed == (_ref("3003"),)
