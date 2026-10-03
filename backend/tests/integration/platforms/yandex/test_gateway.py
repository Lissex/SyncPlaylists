"""YandexGateway на записанных ответах API (tests/fixtures/yandex, respx) — без сети и
без Docker. Обновить записи: tests/tools/record_yandex.py (нужен live-токен)."""

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
import pytest
import respx

from syncplaylists.integrations.platforms.yandex.factory import (
    YandexClientFactory,
    YandexGatewayBuilder,
    YandexProfileFetcher,
)
from syncplaylists.integrations.platforms.yandex.gateway import YandexGateway
from syncplaylists.integrations.platforms.yandex.ids import YandexPlaylistId, YandexTrackId
from syncplaylists.shared_kernel.application.ports import AccountAccess, PlatformCredentials
from syncplaylists.shared_kernel.domain.errors import (
    PlatformAuthError,
    PlatformRateLimitedError,
    PlatformRegionError,
    PlatformUnavailableError,
    PlaylistNotFoundError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.search import InsertOrder, TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    Platform,
    PlaylistRef,
    Transport,
)

API = "https://api.music.yandex.net"
UID = "123456789"
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "yandex"


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def _ref(external_id: str) -> ExternalTrackRef:
    return ExternalTrackRef(Platform.YANDEX, external_id)


class CountingLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[Platform, UUID]] = []
        self.penalties: list[float] = []

    async def acquire(self, platform: Platform, account_id: UUID) -> None:
        self.calls.append((platform, account_id))

    async def penalize(self, platform: Platform, account_id: UUID, seconds: float) -> None:
        self.penalties.append(seconds)

    async def recent_requests(self, platform: Platform, account_id: UUID, minutes: int) -> int:
        return {10: 42, 60: 310}[minutes]


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=API, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def limiter() -> CountingLimiter:
    return CountingLimiter()


@pytest.fixture
def account_id() -> UUID:
    return uuid4()


@pytest.fixture
def gateway(http: httpx.AsyncClient, limiter: CountingLimiter, account_id: UUID) -> YandexGateway:
    clients = YandexClientFactory(http, timeout_seconds=5, limiter=limiter)
    access = AccountAccess(
        account_id=account_id,
        user_id=uuid4(),
        platform=Platform.YANDEX,
        transport=Transport.UNOFFICIAL,
        external_user_id=UID,
        credentials=PlatformCredentials(access_token="y0_test-token"),
    )
    return YandexGatewayBuilder(clients, batch_size=100)(access)


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode())


# --- идентификаторы ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "track_id", "album_id", "text"),
    [("57703:4766", "57703", "4766", "57703:4766"), ("999001", "999001", None, "999001")],
)
def test_track_id_with_and_without_album(
    raw: str, track_id: str, album_id: str | None, text: str
) -> None:
    parsed = YandexTrackId.parse(raw)
    assert (parsed.track_id, parsed.album_id, str(parsed)) == (track_id, album_id, text)


@pytest.mark.parametrize("raw", ["", "abc", "1:2:3", ":5"])
def test_track_id_rejects_garbage(raw: str) -> None:
    with pytest.raises(ValueError, match="id трека"):
        YandexTrackId.parse(raw)


def test_playlist_id_formats() -> None:
    assert YandexPlaylistId.parse("test.user:1001") == YandexPlaylistId(
        owner="test.user", kind=1001
    )
    assert YandexPlaylistId.parse("6ad1ba0c-30fe") == YandexPlaylistId(uuid="6ad1ba0c-30fe")


# --- профиль -------------------------------------------------------------------------


async def test_profile_fetcher_returns_uid_and_display_name(
    api: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    route = api.get("/account/status").respond(json=fixture("account_status"))

    profile = await YandexProfileFetcher(YandexClientFactory(http, timeout_seconds=5)).fetch(
        PlatformCredentials(access_token="y0_token")
    )

    assert profile.external_user_id == UID
    assert profile.display_name == "Test User"
    assert route.calls.last.request.headers["authorization"] == "OAuth y0_token"


async def test_profile_fetcher_rejected_token(
    api: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    api.get("/account/status").respond(401, json={"error": {"name": "session-expired"}})

    with pytest.raises(PlatformAuthError):
        await YandexProfileFetcher(YandexClientFactory(http, timeout_seconds=5)).fetch(
            PlatformCredentials(access_token="expired")
        )


async def test_profile_fetcher_anonymous_account_is_auth_error(
    api: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    anonymous = fixture("account_status")
    del anonymous["result"]["account"]["uid"]
    api.get("/account/status").respond(json=anonymous)

    with pytest.raises(PlatformAuthError):
        await YandexProfileFetcher(YandexClientFactory(http, timeout_seconds=5)).fetch(
            PlatformCredentials(access_token="x")
        )


# --- чтение --------------------------------------------------------------------------


async def test_get_playlist_maps_tracks_and_loads_missing_ones(
    api: respx.MockRouter, gateway: YandexGateway
) -> None:
    api.get(f"/users/{UID}/playlists/1001").respond(json=fixture("playlist"))
    tracks_route = api.post("/tracks").respond(json=fixture("tracks"))

    snapshot = await gateway.get_playlist(PlaylistRef(Platform.YANDEX, f"{UID}:1001"))

    assert snapshot.title == "Дорога"
    assert snapshot.description == "Для машины"
    # Изъятый (available=false) пропущен, порядок сохранён.
    assert [t.ref.external_id for t in snapshot.tracks] == [
        "57703:4766",
        "33311009:4006131",
        "999001",
    ]
    live = snapshot.tracks[0]
    assert live.title == "Starboy (Live)"  # версия — в title, matching её извлечёт
    assert live.artists == ("The Weeknd", "Daft Punk")
    assert live.artist == "The Weeknd, Daft Punk"
    assert live.duration == Duration(260_000)
    assert live.isrc is None
    assert live.cover_url == (
        "https://avatars.yandex.net/get-music-content/49876/9ab1c2d3.a.4766-1/400x400"
    )
    assert snapshot.tracks[1].title == "Группа крови"
    assert snapshot.tracks[2].cover_url is None
    # Догружены только треки без полного объекта — одним запросом.
    assert _form(tracks_route.calls.last.request)["track-ids"] == ["33311009:4006131", "999001"]


async def test_playlist_by_login_and_uuid(api: respx.MockRouter, gateway: YandexGateway) -> None:
    by_login = api.get("/users/test.user/playlists/1001").respond(json=fixture("playlist"))
    by_uuid = api.get("/playlist/11111111-2222-3333-4444-555555555555").respond(
        json=fixture("playlist")
    )

    info = await gateway.playlist_info(PlaylistRef(Platform.YANDEX, "test.user:1001"))
    await gateway.playlist_info(
        PlaylistRef(Platform.YANDEX, "11111111-2222-3333-4444-555555555555")
    )

    assert info.owner_external_id == UID
    assert info.track_count == 4
    assert by_login.called
    assert by_uuid.called


async def test_get_library_newest_first(api: respx.MockRouter, gateway: YandexGateway) -> None:
    api.get(f"/users/{UID}/likes/tracks").respond(json=fixture("likes_tracks"))
    tracks = fixture("tracks")
    tracks["result"] = [tracks["result"][0], fixture("playlist")["result"]["tracks"][0]["track"]]
    api.post("/tracks").respond(json=tracks)

    library = [track async for track in gateway.get_library()]

    assert [t.ref.external_id for t in library] == ["33311009:4006131", "57703:4766"]
    assert gateway.library_insert_order() is InsertOrder.TOP


async def test_search_builds_query_and_skips_unavailable(
    api: respx.MockRouter, gateway: YandexGateway
) -> None:
    route = api.get("/search").respond(json=fixture("search_tracks"))

    results = await gateway.search(TrackQuery(title="Starboy", artist="The Weeknd"), limit=10)

    params = route.calls.last.request.url.params
    assert params["text"] == "The Weeknd Starboy"
    assert params["type"] == "track"
    assert [r.ref.external_id for r in results] == ["57704:4766", "88001:8800"]
    assert results[1].title == "Starboy (Kygo Remix)"


async def test_search_respects_limit(api: respx.MockRouter, gateway: YandexGateway) -> None:
    api.get("/search").respond(json=fixture("search_tracks"))

    assert len(await gateway.search(TrackQuery(title="Starboy"), limit=1)) == 1


async def test_search_by_isrc_is_empty_without_request(
    api: respx.MockRouter, gateway: YandexGateway
) -> None:
    assert await gateway.search_by_isrc(ISRC("USUM71703861")) == []
    assert not api.calls


# --- «своя» медиатека по ссылке users/<login>/playlists/3 ----------------------------


@pytest.mark.parametrize(
    ("external_id", "own"),
    [
        (f"{UID}:3", True),
        ("test.user:3", True),
        ("Test.User:3", True),
        ("someone.else:3", False),
        ("test.user:1001", False),
        ("6ad1ba0c-30fe", False),
    ],
)
async def test_is_own_library(
    api: respx.MockRouter, gateway: YandexGateway, external_id: str, own: bool
) -> None:
    api.get("/account/status").respond(json=fixture("account_status"))

    assert await gateway.is_own_library(PlaylistRef(Platform.YANDEX, external_id)) is own


# --- запись --------------------------------------------------------------------------


async def test_create_private_playlist_with_description(
    api: respx.MockRouter, gateway: YandexGateway
) -> None:
    create = api.post(f"/users/{UID}/playlists/create").respond(json=fixture("playlist_created"))
    describe = api.post(f"/users/{UID}/playlists/1077/description").respond(
        json=fixture("playlist_created")
    )

    ref = await gateway.create_playlist("Перенесено из VK", "Из VK")

    assert ref == PlaylistRef(Platform.YANDEX, f"{UID}:1077")
    assert _form(create.calls.last.request) == {
        "title": ["Перенесено из VK"],
        "visibility": ["private"],
    }
    assert _form(describe.calls.last.request) == {"value": ["Из VK"]}


async def test_add_tracks_skips_present_and_inserts_rest_at_end(
    api: respx.MockRouter, gateway: YandexGateway
) -> None:
    api.get(f"/users/{UID}/playlists/1001").respond(json=fixture("playlist"))
    change = api.post(f"/users/{UID}/playlists/1001/change").respond(json=fixture("playlist"))
    refs = [
        _ref("57703:9999"),  # уже в плейлисте (другой альбом — тот же трек)
        _ref("424242:4242"),
        _ref("424242:1"),  # дубль внутри запроса
        _ref("515151:5151"),
    ]

    result = await gateway.add_tracks(PlaylistRef(Platform.YANDEX, f"{UID}:1001"), refs)

    assert set(result.added) == set(refs)
    assert result.failed == ()
    form = _form(change.calls.last.request)
    assert form["revision"] == ["7"]
    assert json.loads(form["diff"][0]) == [
        {
            "op": "insert",
            "at": 4,
            "tracks": [{"id": "424242", "albumId": "4242"}, {"id": "515151", "albumId": "5151"}],
        }
    ]
    assert change.call_count == 1


async def test_add_tracks_retries_on_wrong_revision(
    api: respx.MockRouter, gateway: YandexGateway
) -> None:
    fresh = fixture("playlist")
    fresh["result"]["revision"] = 8
    fresh["result"]["trackCount"] = 5
    api.get(f"/users/{UID}/playlists/1001").mock(
        side_effect=[httpx.Response(200, json=fixture("playlist")), httpx.Response(200, json=fresh)]
    )
    change = api.post(f"/users/{UID}/playlists/1001/change").mock(
        side_effect=[
            httpx.Response(412, json=fixture("error_wrong_revision")),
            httpx.Response(200, json=fresh),
        ]
    )

    result = await gateway.add_tracks(
        PlaylistRef(Platform.YANDEX, f"{UID}:1001"), [_ref("424242:4242")]
    )

    assert result.added == (_ref("424242:4242"),)
    retried = _form(change.calls.last.request)
    assert retried["revision"] == ["8"]
    assert json.loads(retried["diff"][0])[0]["at"] == 5


async def test_add_tracks_to_foreign_playlist_is_not_writable_without_change(
    api: respx.MockRouter, gateway: YandexGateway
) -> None:
    foreign = fixture("playlist")
    foreign["result"]["owner"]["uid"] = 1
    api.get("/users/someone/playlists/5").respond(json=foreign)
    change = api.post(url__regex=r".*/change$")

    with pytest.raises(PlaylistNotWritableError):
        await gateway.add_tracks(PlaylistRef(Platform.YANDEX, "someone:5"), [_ref("1:1")])
    assert not change.called


async def test_add_tracks_forbidden_change_is_not_writable(
    api: respx.MockRouter, gateway: YandexGateway
) -> None:
    api.get(f"/users/{UID}/playlists/1001").respond(json=fixture("playlist"))
    api.post(f"/users/{UID}/playlists/1001/change").respond(
        403, json={"error": {"name": "forbidden"}}
    )

    with pytest.raises(PlaylistNotWritableError):
        await gateway.add_tracks(PlaylistRef(Platform.YANDEX, f"{UID}:1001"), [_ref("1:1")])


async def test_add_tracks_without_album_inserted_one_by_one_and_rejection_is_failed(
    api: respx.MockRouter, gateway: YandexGateway
) -> None:
    api.get(f"/users/{UID}/playlists/1001").respond(json=fixture("playlist"))
    change = api.post(f"/users/{UID}/playlists/1001/change").mock(
        side_effect=[
            httpx.Response(200, json=fixture("playlist")),  # пачка с альбомами
            httpx.Response(400, json={"error": {"name": "validation"}}),  # трек без альбома
        ]
    )

    result = await gateway.add_tracks(
        PlaylistRef(Platform.YANDEX, f"{UID}:1001"), [_ref("700"), _ref("424242:4242")]
    )

    assert result.added == (_ref("424242:4242"),)
    assert result.failed == (_ref("700"),)
    assert change.call_count == 2


async def test_add_to_library_one_by_one_mode_in_given_order_and_skips_liked(
    api: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    gateway = YandexGateway(
        YandexClientFactory(http, timeout_seconds=5).client("t"),
        UID,
        library_batch_preserves_order=False,
    )
    api.get(f"/users/{UID}/likes/tracks").respond(json=fixture("likes_tracks"))
    like = api.post(f"/users/{UID}/likes/tracks/add-multiple").respond(
        json={"result": {"revision": 4243}}
    )
    refs = [_ref("1:10"), _ref("33311009:777"), _ref("2:20")]  # 33311009 уже лайкнут

    result = await gateway.add_to_library(refs)

    assert result.added == (_ref("33311009:777"), _ref("1:10"), _ref("2:20"))
    assert [_form(call.request)["track-ids"] for call in like.calls] == [["1:10"], ["2:20"]]


async def test_add_to_library_batches_are_sent_reversed_so_last_ends_on_top(
    api: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    # Яндекс кладёт пачку так, что ПЕРВЫЙ трек пачки — сверху; следующая пачка — над
    # предыдущей. Чтобы последний трек списка оказался на самом верху, каждая пачка
    # отправляется развёрнутой.
    gateway = YandexGateway(
        YandexClientFactory(http, timeout_seconds=5).client("t"), UID, batch_size=2
    )
    api.get(f"/users/{UID}/likes/tracks").respond(json=fixture("likes_tracks"))
    like = api.post(f"/users/{UID}/likes/tracks/add-multiple").respond(
        json={"result": {"revision": 1}}
    )

    result = await gateway.add_to_library([_ref("1:10"), _ref("2:20"), _ref("3:30")])

    assert result.added == (_ref("1:10"), _ref("2:20"), _ref("3:30"))
    assert [_form(call.request)["track-ids"] for call in like.calls] == [
        ["2:20", "1:10"],
        ["3:30"],
    ]


# --- ошибки площадки → доменные ошибки ------------------------------------------------


@pytest.mark.parametrize(
    ("status", "response_kwargs", "error"),
    [
        (401, {"json": {"error": {"name": "session-expired"}}}, PlatformAuthError),
        (429, {"headers": {"Retry-After": "7"}}, PlatformRateLimitedError),
        (500, {}, PlatformUnavailableError),
        (503, {"text": "<html>"}, PlatformUnavailableError),
        (451, {"json": {"error": {"name": "not-allowed"}}}, PlatformRegionError),
        (404, {"json": {"error": {"name": "playlist-not-found"}}}, PlaylistNotFoundError),
        # 403 на чтение — закрытый чужой плейлист, а НЕ протухший токен.
        (403, {"json": {"error": {"name": "forbidden"}}}, PlaylistNotFoundError),
    ],
)
async def test_http_errors_become_domain_errors(
    api: respx.MockRouter,
    gateway: YandexGateway,
    status: int,
    response_kwargs: dict[str, Any],
    error: type[Exception],
) -> None:
    api.get("/users/x/playlists/5").respond(status, **response_kwargs)

    with pytest.raises(error):
        await gateway.get_playlist(PlaylistRef(Platform.YANDEX, "x:5"))


async def test_rate_limited_carries_retry_after(
    api: respx.MockRouter, gateway: YandexGateway
) -> None:
    api.get("/search").respond(429, headers={"Retry-After": "7"})

    with pytest.raises(PlatformRateLimitedError) as caught:
        await gateway.search(TrackQuery(title="x"))
    assert caught.value.retry_after_seconds == 7


async def test_network_errors_are_unavailable(
    api: respx.MockRouter, gateway: YandexGateway
) -> None:
    api.get("/search").mock(side_effect=httpx.ConnectTimeout("timeout"))

    with pytest.raises(PlatformUnavailableError):
        await gateway.search(TrackQuery(title="x"))


async def test_every_request_goes_through_rate_limiter(
    api: respx.MockRouter, gateway: YandexGateway, limiter: CountingLimiter, account_id: UUID
) -> None:
    api.get(f"/users/{UID}/playlists/1001").respond(json=fixture("playlist"))
    api.post("/tracks").respond(json=fixture("tracks"))

    await gateway.get_playlist(PlaylistRef(Platform.YANDEX, f"{UID}:1001"))

    assert limiter.calls == [(Platform.YANDEX, account_id)] * 2


async def test_429_pauses_whole_account_for_retry_after(
    api: respx.MockRouter, gateway: YandexGateway, limiter: CountingLimiter
) -> None:
    api.get("/search").respond(429, headers={"Retry-After": "600"})

    with pytest.raises(PlatformRateLimitedError):
        await gateway.search(TrackQuery(title="x"))

    assert limiter.penalties == [600]


async def test_other_errors_do_not_pause_account(
    api: respx.MockRouter, gateway: YandexGateway, limiter: CountingLimiter
) -> None:
    api.get("/search").respond(503)

    with pytest.raises(PlatformUnavailableError):
        await gateway.search(TrackQuery(title="x"))

    assert limiter.penalties == []


async def test_429_log_has_request_counts_but_no_token(
    api: respx.MockRouter, gateway: YandexGateway, caplog: pytest.LogCaptureFixture
) -> None:
    api.get("/search").respond(429, headers={"Retry-After": "600"})

    with caplog.at_level("WARNING"), pytest.raises(PlatformRateLimitedError):
        await gateway.search(TrackQuery(title="x"))

    assert "42 запросов за 10 мин, 310 за 60 мин, Retry-After 600" in caplog.text
    assert "y0_test-token" not in caplog.text
