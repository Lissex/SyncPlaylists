from syncplaylists.integrations.platforms.search_cache import CachedSearchGateway
from syncplaylists.shared_kernel.domain.search import PlaylistSnapshot, TrackCandidate, TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    Platform,
    PlaylistRef,
)
from tests.fakes import FakeMusicPlatformGateway

_HIT = TrackCandidate(
    ref=ExternalTrackRef(Platform.YANDEX, "57703:4766"),
    title="Starboy (Live)",
    artist="The Weeknd, Daft Punk",
    duration=Duration(230_000),
    isrc=ISRC("USUM71703861"),
    artists=("The Weeknd", "Daft Punk"),
    cover_url="https://avatars.yandex.net/x/400x400",
)


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.fail = False

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise ConnectionError("redis down")
        return self.data.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.data[key] = value
        self.ttls[key] = ttl_seconds


def _gateway(results: list[TrackCandidate] | None = None) -> FakeMusicPlatformGateway:
    return FakeMusicPlatformGateway(
        platform=Platform.YANDEX,
        search_results=[_HIT] if results is None else results,
        isrc_results={"USUM71703861": [_HIT]},
    )


async def test_repeated_search_is_served_from_cache_with_all_fields() -> None:
    inner = _gateway()
    cached = CachedSearchGateway(inner, MemoryCache(), ttl_seconds=86400)

    first = await cached.search(TrackQuery(title="Starboy", artist="The Weeknd"))
    second = await cached.search(TrackQuery(title="  starboy ", artist="THE WEEKND"))

    assert first == second == [_HIT]
    assert inner.search_calls == 1


async def test_different_queries_and_limits_use_different_keys() -> None:
    inner = _gateway()
    cached = CachedSearchGateway(inner, MemoryCache(), ttl_seconds=86400)

    await cached.search(TrackQuery(title="Starboy", artist="The Weeknd"))
    await cached.search(TrackQuery(title="Starboy", artist="Kygo"))
    await cached.search(TrackQuery(title="Starboy live", artist="The Weeknd"))
    await cached.search(TrackQuery(title="Starboy", artist="The Weeknd"), limit=5)

    assert inner.search_calls == 4


async def test_search_by_isrc_is_cached() -> None:
    inner = _gateway()
    cached = CachedSearchGateway(inner, MemoryCache(), ttl_seconds=86400)

    await cached.search_by_isrc(ISRC("USUM71703861"))
    assert await cached.search_by_isrc(ISRC("USUM71703861")) == [_HIT]

    assert inner.search_by_isrc_calls == 1


async def test_empty_result_is_cached_for_shorter_time() -> None:
    cache = MemoryCache()
    inner = _gateway(results=[])
    cached = CachedSearchGateway(inner, cache, ttl_seconds=86400)

    assert await cached.search(TrackQuery(title="Nothing")) == []
    assert await cached.search(TrackQuery(title="Nothing")) == []

    assert inner.search_calls == 1
    assert list(cache.ttls.values()) == [3600]


async def test_cache_failure_falls_back_to_platform() -> None:
    cache = MemoryCache()
    cache.fail = True
    inner = _gateway()
    cached = CachedSearchGateway(inner, cache, ttl_seconds=86400)

    assert await cached.search(TrackQuery(title="Starboy")) == [_HIT]
    assert await cached.search(TrackQuery(title="Starboy")) == [_HIT]
    assert inner.search_calls == 2


async def test_other_methods_are_passed_through() -> None:
    snapshot = PlaylistSnapshot(
        ref=PlaylistRef(Platform.YANDEX, "u:1"), title="t", description=None, tracks=(_HIT,)
    )
    inner = FakeMusicPlatformGateway(platform=Platform.YANDEX, playlist=snapshot, library=[_HIT])
    cached = CachedSearchGateway(inner, MemoryCache(), ttl_seconds=86400)
    ref = PlaylistRef(Platform.YANDEX, "u:1")

    assert cached.platform is Platform.YANDEX
    assert await cached.get_playlist(ref) == snapshot
    assert [t async for t in cached.get_library()] == [_HIT]
    await cached.add_tracks(ref, [_HIT.ref])
    await cached.add_to_library([_HIT.ref])
    assert inner.added_to_playlist == [_HIT.ref]
    assert inner.added_to_library == [_HIT.ref]
    assert cached.library_insert_order() is inner.library_insert_order()
