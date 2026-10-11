"""Яндекс через расширение (4c-4): тот же YandexGateway поверх ExtensionYandexClient.
Канал — ScriptedChannel (ответы-проекции, как их отдаёт страница), без сети и без Docker.
Проекции строятся из тех же записанных ответов API, что у серверного пути, — кандидаты
должны совпасть."""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx

from syncplaylists.bootstrap.container import GatewayProvider
from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.integrations.platforms.soundcloud.client_id import ClientIdProvider
from syncplaylists.integrations.platforms.soundcloud.factory import SoundCloudApiFactory
from syncplaylists.integrations.platforms.soundcloud.tokens import TokenEndpoint
from syncplaylists.integrations.platforms.yandex.extension_client import ExtensionYandexClient
from syncplaylists.integrations.platforms.yandex.factory import (
    YandexClientFactory,
    YandexExtensionGatewayBuilder,
    YandexGatewayBuilder,
)
from syncplaylists.integrations.platforms.yandex.gateway import YandexGateway
from syncplaylists.modules.extension.application.operations import ExtensionOperationError
from syncplaylists.shared_kernel.application.ports import AccountAccess, PlatformCredentials
from syncplaylists.shared_kernel.domain.errors import (
    ExtensionUnavailableError,
    ExtensionUnavailableReason,
    PlatformUnavailableError,
    PlaylistNotFoundError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.search import TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    Platform,
    PlaylistRef,
    Transport,
)
from tests.fakes.extension import ScriptedChannel
from tests.fakes.platform import InMemoryWriteGuard
from tests.integration.platforms.yandex.test_gateway import API, UID, CountingLimiter, fixture

_PLAYLIST = PlaylistRef(Platform.YANDEX, f"{UID}:1001")


def _access(transport: Transport = Transport.EXTENSION) -> AccountAccess:
    return AccountAccess(
        account_id=uuid4(),
        user_id=uuid4(),
        platform=Platform.YANDEX,
        transport=transport,
        external_user_id=UID,
        credentials=(
            PlatformCredentials(access_token="y0_test-token")
            if transport is Transport.UNOFFICIAL
            else None
        ),
    )


# Проекция, как её делает страница (extension/src/background/yandex.ts): id — строками,
# только поля, которые читает mapping.py.
def _track(raw: dict[str, Any]) -> dict[str, Any]:
    keep = ("title", "version", "available", "durationMs", "coverUri", "ogImage")
    track: dict[str, Any] = {"id": str(raw["id"])}
    track.update({key: raw[key] for key in keep if raw.get(key) is not None})
    track["artists"] = [{"id": str(a["id"]), "name": a["name"]} for a in raw.get("artists") or []]
    track["albums"] = [{"id": str(a["id"])} for a in raw.get("albums") or []]
    return track


def _short(raw: dict[str, Any]) -> dict[str, Any]:
    short = {"id": str(raw["id"]), "timestamp": raw.get("timestamp") or ""}
    if raw.get("albumId") is not None:
        short["albumId"] = str(raw["albumId"])
    return short


def _playlist_page(
    raw: dict[str, Any], start: int = 0, size: int = 5000, **override: Any
) -> dict[str, Any]:
    shorts = [_short(s) for s in raw["tracks"]]
    page: dict[str, Any] = {
        "kind": raw["kind"],
        "uid": str(raw["uid"]),
        "owner": {"uid": str(raw["owner"]["uid"]), "login": raw["owner"]["login"]},
        "title": raw["title"],
        "revision": raw["revision"],
        "trackCount": raw["trackCount"],
        "tracks": shorts[start : start + size],
    }
    if raw.get("description"):
        page["description"] = raw["description"]
    if start + size < len(shorts):
        page["nextOffset"] = start + size
    page.update(override)
    return page


def _all_tracks() -> dict[str, Any]:
    """Полные треки фикстур по id — то, что страница отдала бы на `tracks`."""
    raw = [*fixture("tracks")["result"]]
    raw += [s["track"] for s in fixture("playlist")["result"]["tracks"] if s.get("track")]
    return {str(t["id"]): _track(t) for t in raw}


def _tracks_reply(ids: list[str]) -> dict[str, Any]:
    known = _all_tracks()
    return {"tracks": [known[i.partition(":")[0]] for i in ids]}


class TracksChannel(ScriptedChannel):
    """ScriptedChannel, который на `tracks` отвечает по запрошенным id."""

    async def call(self, call: Any) -> Any:
        if call.operation == "tracks" and "tracks" not in self.responses:
            self.calls.append(call)
            return _tracks_reply(list(call.args["ids"]))
        return await super().call(call)


@pytest.fixture
def channel() -> TracksChannel:
    return TracksChannel()


@pytest.fixture
def gateway(channel: TracksChannel) -> YandexGateway:
    return YandexExtensionGatewayBuilder(channel, batch_size=100)(_access())


def _ops(channel: ScriptedChannel) -> list[str]:
    return [call.operation for call in channel.calls]


def _ref(external_id: str) -> ExternalTrackRef:
    return ExternalTrackRef(Platform.YANDEX, external_id)


@pytest.fixture
async def server_gateway() -> AsyncIterator[YandexGateway]:
    async with httpx.AsyncClient() as http:
        yield YandexGatewayBuilder(YandexClientFactory(http, timeout_seconds=5), batch_size=100)(
            _access(Transport.UNOFFICIAL)
        )


# --- те же кандидаты, что у серверного пути -------------------------------------------


async def test_playlist_candidates_match_server_path(
    gateway: YandexGateway, channel: TracksChannel, server_gateway: YandexGateway
) -> None:
    raw = fixture("playlist")["result"]
    channel.reply("playlist_page", _playlist_page(raw))
    with respx.mock(base_url=API) as api:
        api.get(f"/users/{UID}/playlists/1001").respond(json=fixture("playlist"))
        api.post("/tracks").respond(json=fixture("tracks"))
        expected = await server_gateway.get_playlist(_PLAYLIST)

    snapshot = await gateway.get_playlist(_PLAYLIST)

    assert snapshot == expected
    # Записи плейлиста — без вложенных треков: все догружены через `tracks`.
    assert _ops(channel) == ["playlist_page", "tracks"]
    assert channel.calls[0].args == {
        "owner": UID,
        "kind": 1001,
        "uuid": None,
        "offset": 0,
        "limit": 5000,
        "revision": None,
    }


async def test_search_candidates_match_server_path(
    gateway: YandexGateway, channel: TracksChannel, server_gateway: YandexGateway
) -> None:
    raw = fixture("search_tracks")["result"]["tracks"]["results"]
    channel.reply("search", {"tracks": [_track(t) for t in raw]})
    query = TrackQuery(title="Starboy", artist="The Weeknd")
    with respx.mock(base_url=API) as api:
        api.get("/search").respond(json=fixture("search_tracks"))
        expected = await server_gateway.search(query)

    found = await gateway.search(query)

    assert found == expected
    assert [c.ref.external_id for c in found] == ["57704:4766", "88001:8800"]
    assert channel.calls[0].args == {"text": "The Weeknd Starboy"}


async def test_playlist_is_read_page_by_page_with_revision_of_first_page(
    channel: TracksChannel,
) -> None:
    gateway = YandexGateway(ExtensionYandexClient(channel, _access(), page_size=2), UID)
    raw = fixture("playlist")["result"]
    channel.reply("playlist_page", _playlist_page(raw, 0, 2), _playlist_page(raw, 2, 2))

    snapshot = await gateway.get_playlist(_PLAYLIST)

    assert len(snapshot.tracks) == 3  # четвёртый трек изъят (available=false)
    pages = [c.args for c in channel.calls if c.operation == "playlist_page"]
    assert [(p["offset"], p["limit"], p["revision"]) for p in pages] == [(0, 2, None), (2, 2, 7)]


async def test_list_changed_between_pages_is_read_again(channel: TracksChannel) -> None:
    gateway = YandexExtensionGatewayBuilder(channel, batch_size=100)(_access())
    likes = fixture("likes_tracks")["result"]["library"]
    shorts = [_short(s) for s in likes["tracks"]]
    channel.reply(
        "library_ids_page",
        {"revision": 4242, "tracks": shorts[:1], "nextOffset": 1},
        ExtensionOperationError(Platform.YANDEX, "conflict"),  # новый лайк между страницами
        {"revision": 4243, "tracks": shorts},
    )

    library = [t.ref.external_id async for t in gateway.get_library()]

    assert library == ["33311009:4006131", "57703:4766"]
    assert [c.args["offset"] for c in channel.calls if c.operation == "library_ids_page"] == [
        0,
        1,
        0,
    ]


async def test_page_without_progress_is_an_error_not_a_loop(
    gateway: YandexGateway, channel: TracksChannel
) -> None:
    raw = fixture("playlist")["result"]
    channel.reply("playlist_page", _playlist_page(raw, 0, 1, nextOffset=0))

    with pytest.raises(PlatformUnavailableError, match="без сдвига"):
        await gateway.get_playlist(_PLAYLIST)


async def test_extra_field_in_projection_is_rejected(
    gateway: YandexGateway, channel: TracksChannel
) -> None:
    track = _track(fixture("tracks")["result"][0])
    channel.reply("search", {"tracks": [{**track, "token": "leak"}]})

    with pytest.raises(PlatformUnavailableError, match="не по схеме"):
        await gateway.search(TrackQuery(title="x"))


# --- запись ---------------------------------------------------------------------------


async def test_create_is_idempotent_by_request_id_and_private(
    gateway: YandexGateway, channel: TracksChannel
) -> None:
    channel.reply("create_playlist", {"kind": 1077, "revision": 1})
    channel.reply("set_description", {})

    ref = await gateway.create_playlist("Лайки SoundCloud", "Из SoundCloud", request_id="t-1:1")

    assert ref == PlaylistRef(Platform.YANDEX, f"{UID}:1077")
    create, describe = channel.calls
    assert create.args == {"title": "Лайки SoundCloud"}
    assert create.idempotency_key == "create_playlist:t-1:1"
    assert describe.args == {"kind": 1077, "description": "Из SoundCloud"}


async def test_add_tracks_sends_structured_insert_not_raw_diff(
    gateway: YandexGateway, channel: TracksChannel
) -> None:
    raw = fixture("playlist")["result"]
    channel.reply("playlist_page", _playlist_page(raw))
    channel.reply("add_tracks", {"revision": 8, "trackCount": 6})

    result = await gateway.add_tracks(
        _PLAYLIST, [_ref("57703:9999"), _ref("424242:4242"), _ref("515151:5151")]
    )

    assert set(result.added) == {_ref("57703:9999"), _ref("424242:4242"), _ref("515151:5151")}
    [change] = [c for c in channel.calls if c.operation == "add_tracks"]
    assert change.args == {
        "kind": 1001,
        "revision": 7,
        "at": 4,
        "tracks": [
            {"id": "424242", "album_id": "4242"},
            {"id": "515151", "album_id": "5151"},
        ],
    }
    assert change.idempotency_key is None  # повтор безопасен: ревизия + отсев дублей


async def test_add_tracks_conflict_rereads_and_retries_without_duplicates(
    gateway: YandexGateway, channel: TracksChannel
) -> None:
    raw = fixture("playlist")["result"]
    # Пачка дошла до Яндекса, ответ потерян; повтор с той же ревизией → conflict,
    # перечитанный плейлист уже содержит трек.
    after = json.loads(json.dumps(raw))
    after["revision"] = 8
    after["trackCount"] = 5
    after["tracks"].append({"id": 424242, "albumId": 4242, "timestamp": ""})
    channel.reply("playlist_page", _playlist_page(raw), _playlist_page(after))
    channel.reply(
        "add_tracks",
        ExtensionOperationError(Platform.YANDEX, "conflict"),
        {"revision": 9, "trackCount": 6},
    )

    result = await gateway.add_tracks(_PLAYLIST, [_ref("424242:4242")])

    assert result.added == (_ref("424242:4242"),)
    retry = [c for c in channel.calls if c.operation == "add_tracks"][-1]
    assert (retry.args["revision"], retry.args["at"]) == (8, 5)


async def test_track_without_album_rejected_is_failed(
    gateway: YandexGateway, channel: TracksChannel
) -> None:
    channel.reply("playlist_page", _playlist_page(fixture("playlist")["result"]))
    channel.reply("add_tracks", ExtensionOperationError(Platform.YANDEX, "bad_request"))

    result = await gateway.add_tracks(_PLAYLIST, [_ref("700")])

    assert result.failed == (_ref("700"),)


async def test_forbidden_write_is_not_writable_and_closed_read_is_not_found(
    gateway: YandexGateway, channel: TracksChannel
) -> None:
    channel.reply("playlist_page", PlaylistNotWritableError(Platform.YANDEX, "403"))
    with pytest.raises(PlaylistNotFoundError):
        await gateway.get_playlist(_PLAYLIST)

    channel.reply("playlist_page", _playlist_page(fixture("playlist")["result"]))
    channel.reply("add_tracks", PlaylistNotWritableError(Platform.YANDEX, "403"))
    with pytest.raises(PlaylistNotWritableError):
        await gateway.add_tracks(_PLAYLIST, [_ref("1:1")])


async def test_like_many_in_reversed_batches_and_skips_liked(
    channel: TracksChannel,
) -> None:
    gateway = YandexExtensionGatewayBuilder(channel, batch_size=2)(_access())
    likes = fixture("likes_tracks")["result"]["library"]
    channel.reply(
        "library_ids_page", {"revision": 1, "tracks": [_short(s) for s in likes["tracks"]]}
    )
    channel.reply("like_many", {}, {})

    result = await gateway.add_to_library(
        [_ref("1:10"), _ref("33311009:777"), _ref("2:20"), _ref("3:30")]
    )

    assert set(result.added) == {_ref("1:10"), _ref("33311009:777"), _ref("2:20"), _ref("3:30")}
    assert [c.args["ids"] for c in channel.calls if c.operation == "like_many"] == [
        ["2:20", "1:10"],
        ["3:30"],
    ]


async def test_captcha_pauses_for_client(gateway: YandexGateway, channel: TracksChannel) -> None:
    channel.reply(
        "search", ExtensionUnavailableError(Platform.YANDEX, ExtensionUnavailableReason.CAPTCHA)
    )

    with pytest.raises(ExtensionUnavailableError) as raised:
        await gateway.search(TrackQuery(title="Starboy"))

    assert raised.value.reason is ExtensionUnavailableReason.CAPTCHA


# --- общий серверный bucket к расширению не применяется (11g) ------------------------


class _NoCache:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        return None


async def test_container_search_via_extension_bypasses_server_limiter(
    channel: TracksChannel,
) -> None:
    settings = Settings(
        _env_file=None,
        db={"dsn": "postgresql+asyncpg://u:p@localhost:5432/db"},
        redis={"dsn": "redis://localhost:6379/0"},
        security={"token_encryption_key": "A" * 43 + "="},
    )
    limiter = CountingLimiter()
    async with httpx.AsyncClient() as http:
        soundcloud = SoundCloudApiFactory(
            http,
            ClientIdProvider(
                http, _NoCache(), ttl_seconds=60, min_refresh_seconds=60, timeout_seconds=1
            ),
            TokenEndpoint(http, timeout_seconds=1),
            timeout_seconds=1,
        )
        build = GatewayProvider.__dict__["get_gateway_factory"].origin
        factory = build(
            GatewayProvider(),
            settings,
            YandexClientFactory(http, timeout_seconds=5, limiter=limiter),
            soundcloud,
            _NoCache(),
            channel,
            InMemoryWriteGuard(),
        )
        raw = fixture("search_tracks")["result"]["tracks"]["results"]
        channel.reply("search", {"tracks": [_track(t) for t in raw]})

        with respx.mock(base_url=API) as api:
            found = await factory.for_account(_access()).search(TrackQuery(title="Starboy"))
            assert not api.calls

    assert found
    assert _ops(channel) == ["search"]
    assert limiter.calls == []
    assert limiter.counted == 0


async def test_is_own_library_checks_login_from_whoami(
    gateway: YandexGateway, channel: TracksChannel
) -> None:
    channel.reply("whoami", {"id": UID, "username": "Test User", "login": "test.user"})

    assert await gateway.is_own_library(PlaylistRef(Platform.YANDEX, "Test.User:3"))
    assert await gateway.is_own_library(PlaylistRef(Platform.YANDEX, f"{UID}:3"))
    assert _ops(channel) == ["whoami"]  # логин запрошен один раз
