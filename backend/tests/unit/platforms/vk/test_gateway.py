"""VkGateway (4c-5) на фейковом VkApi: плейлист пишется заменой списка целиком, копии
узнаются без дублей, непринятый трек ищется делением пачки пополам, «Моя музыка» —
в пределах суточного бюджета, flood control — нарастающая пауза."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from syncplaylists.integrations.platforms.vk.api import VkBadRequestError
from syncplaylists.integrations.platforms.vk.extension_wire import (
    VkAccount,
    VkAdded,
    VkCreated,
    VkPlaylist,
    VkTrack,
)
from syncplaylists.integrations.platforms.vk.gateway import (
    DAILY_LIMIT_REASON,
    FLOOD_REASON,
    VkGateway,
    VkLimits,
)
from syncplaylists.integrations.platforms.vk.ids import VkPlaylistId, VkTrackId
from syncplaylists.shared_kernel.domain.errors import (
    PlatformRateLimitedError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.search import InsertOrder, TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform, PlaylistRef
from tests.fakes.platform import InMemoryWriteGuard

_ME = 100500


def _track(owner_id: int, audio_id: int, title: str = "", **extra: object) -> VkTrack:
    return VkTrack.model_validate(
        {
            "owner_id": owner_id,
            "id": audio_id,
            "artist": "Artist",
            "title": title or f"Song {audio_id}",
            "duration": 200 + audio_id % 50,
            **extra,
        }
    )


@dataclass
class FakeVkApi:
    """VK «в браузере»: каталог треков, плейлисты пользователя (копии с новыми id, как у
    VK), «Моя музыка» (новое сверху). bad — id, которые VK не берёт в плейлист."""

    catalog: dict[str, VkTrack] = field(default_factory=dict)
    playlists: dict[int, list[str]] = field(default_factory=dict)
    headers: dict[int, VkPlaylist] = field(default_factory=dict)
    saved: list[str] = field(default_factory=list)
    bad: set[str] = field(default_factory=set)
    add_errors: list[Exception] = field(default_factory=list)
    set_calls: list[list[str]] = field(default_factory=list)
    keys: list[str | None] = field(default_factory=list)
    next_copy: int = 900

    def _copy(self, plain: str) -> str:
        original = self.catalog[plain]
        self.next_copy += 1
        release = original.release_audio_id or (plain if original.owner_id < 0 else None)
        copy = original.model_copy(
            update={"owner_id": _ME, "id": self.next_copy, "release_audio_id": release}
        )
        self.catalog[copy.plain] = copy
        return copy.plain

    async def whoami(self) -> VkAccount:
        return VkAccount(id=str(_ME))

    async def search(self, text: str, count: int) -> list[VkTrack]:
        return list(self.catalog.values())[:count]

    async def tracks(self, ids: Sequence[VkTrackId]) -> list[VkTrack]:
        return [self.catalog[i.plain] for i in ids if i.plain in self.catalog]

    async def playlist_info(self, playlist: VkPlaylistId) -> VkPlaylist:
        return self.headers[playlist.playlist_id]

    async def playlist(self, playlist: VkPlaylistId) -> tuple[VkPlaylist, list[VkTrack]]:
        items = self.playlists[playlist.playlist_id]
        return self.headers[playlist.playlist_id], [self.catalog[i] for i in items]

    async def library(self) -> AsyncIterator[VkTrack]:
        for plain in self.saved:
            yield self.catalog[plain]

    async def library_snapshot(self) -> list[VkTrack]:
        return [self.catalog[plain] for plain in self.saved]

    async def create_playlist(
        self, title: str, description: str, *, idempotency_key: str | None
    ) -> VkCreated:
        playlist_id = 158 + len(self.playlists)
        self.playlists[playlist_id] = []
        self.headers[playlist_id] = VkPlaylist(
            owner_id=_ME, id=playlist_id, access_key="abc", title=title, editable=True
        )
        self.keys.append(idempotency_key)
        return VkCreated(owner_id=_ME, id=playlist_id, access_key="abc")

    async def set_playlist_tracks(
        self, playlist_id: int, audio_ids: Sequence[str], *, idempotency_key: str | None
    ) -> None:
        self.set_calls.append(list(audio_ids))
        self.keys.append(idempotency_key)
        if self.bad & set(audio_ids):
            raise VkBadRequestError(Platform.VK, "bad_request")
        self.playlists[playlist_id] = [
            plain if plain.startswith(f"{_ME}_") else self._copy(plain) for plain in audio_ids
        ]

    async def add_to_library(self, track: VkTrackId, *, idempotency_key: str | None) -> VkAdded:
        if self.add_errors:
            raise self.add_errors.pop(0)
        self.keys.append(idempotency_key)
        plain = self._copy(track.plain)
        self.saved.insert(0, plain)
        copy = self.catalog[plain]
        return VkAdded(owner_id=copy.owner_id, id=copy.id)


def _gateway(
    api: FakeVkApi, guard: InMemoryWriteGuard | None = None, **limits: object
) -> VkGateway:
    return VkGateway(api, str(_ME), uuid4(), guard or InMemoryWriteGuard(), VkLimits(**limits))  # type: ignore[arg-type]


def _ref(track: VkTrack) -> ExternalTrackRef:
    return ExternalTrackRef(Platform.VK, track.plain)


def _catalog(*tracks: VkTrack) -> dict[str, VkTrack]:
    return {t.plain: t for t in tracks}


def _titles(api: FakeVkApi, playlist_id: int) -> list[str]:
    return [api.catalog[i].title for i in api.playlists[playlist_id]]


async def test_search_skips_own_copies_and_marks_official() -> None:
    official = _track(-2001, 1, "Hit", main_artists=["Artist"], release_audio_id="-2001_1")
    upload = _track(42, 2, "Hit")
    own = _track(_ME, 3, "Hit")
    api = FakeVkApi(catalog=_catalog(own, upload, official))

    found = await _gateway(api).search(TrackQuery(title="Hit", artist="Artist"))

    assert [c.ref.external_id for c in found] == ["42_2", "-2001_1"]
    assert [c.rights_holder for c in found] == [False, True]


async def test_new_playlist_gets_tracks_in_order_without_copies_of_present() -> None:
    a, b, c = _track(-1, 1, "A", release_audio_id="-1_1"), _track(2, 2, "B"), _track(3, 3, "C")
    api = FakeVkApi(catalog=_catalog(a, b, c))
    gateway = _gateway(api)

    ref = await gateway.create_playlist("Перенос", None, request_id="t-1:1")
    assert ref == PlaylistRef(Platform.VK, f"{_ME}_158_abc")
    assert api.keys == ["create_playlist:t-1:1"]

    first = await gateway.add_tracks(ref, [_ref(a), _ref(b)])
    # Повтор пачки (ответ потерян) + новый трек: копии a и b уже в плейлисте.
    second = await gateway.add_tracks(ref, [_ref(a), _ref(b), _ref(c)])

    assert _titles(api, 158) == ["A", "B", "C"]
    assert set(first.added) == {_ref(a), _ref(b)}
    assert set(second.added) == {_ref(a), _ref(b), _ref(c)}
    assert not first.failed
    assert not second.failed
    # Вторая запись — тот же список копий + новый трек в конце.
    assert api.set_calls[-1][:2] == api.playlists[158][:2]
    assert api.set_calls[-1][2] == "3_3"


async def test_rejected_track_is_found_by_halving_rest_is_written() -> None:
    tracks = [_track(10 + i, i, f"T{i}") for i in range(5)]
    api = FakeVkApi(catalog=_catalog(*tracks), bad={"13_3"})
    gateway = _gateway(api)
    ref = await gateway.create_playlist("P", None)

    result = await gateway.add_tracks(ref, [_ref(t) for t in tracks])

    assert result.failed == (_ref(tracks[3]),)
    assert set(result.added) == {_ref(t) for i, t in enumerate(tracks) if i != 3}
    assert _titles(api, 158) == ["T0", "T1", "T2", "T4"]
    # Каждая попытка — замена целиком, без проблемного трека в итоге.
    assert all("13_3" not in call for call in api.set_calls[-1:])


async def test_foreign_or_generated_playlist_is_not_writable() -> None:
    api = FakeVkApi()
    api.headers[1] = VkPlaylist(owner_id=777, id=1, editable=True)
    api.headers[2] = VkPlaylist(owner_id=_ME, id=-24, type="generated", editable=False)
    api.playlists = {1: [], 2: []}
    gateway = _gateway(api)

    with pytest.raises(PlaylistNotWritableError):
        await gateway.add_tracks(PlaylistRef(Platform.VK, "777_1"), [])
    with pytest.raises(PlaylistNotWritableError):
        await gateway.add_tracks(PlaylistRef(Platform.VK, f"{_ME}_2"), [])


async def test_own_ugc_playlist_without_edit_flag_is_writable() -> None:
    # Только что созданный плейлист VK может прийти без permissions.edit (e2e 4c-5).
    track = _track(7, 7, "X")
    api = FakeVkApi(catalog=_catalog(track))
    api.headers[1] = VkPlaylist(owner_id=_ME, id=1, type="ugc", editable=False)
    api.playlists = {1: []}

    result = await _gateway(api).add_tracks(PlaylistRef(Platform.VK, f"{_ME}_1"), [_ref(track)])

    assert result.added == (_ref(track),)
    assert api.playlists[1] != []


async def test_library_skips_present_and_puts_new_on_top() -> None:
    a = _track(-1, 1, "A", release_audio_id="-1_1", main_artists=["Artist"])
    b, c = _track(2, 2, "B"), _track(3, 3, "C")
    api = FakeVkApi(catalog=_catalog(a, b, c))
    api.saved = [api._copy("-1_1")]  # «A» уже в «Моей музыке» (копия)
    gateway = _gateway(api)
    assert gateway.library_insert_order() is InsertOrder.TOP

    result = await gateway.add_to_library([_ref(a), _ref(b), _ref(c)])

    assert set(result.added) == {_ref(a), _ref(b), _ref(c)}
    assert [api.catalog[i].title for i in api.saved] == ["C", "B", "A"]
    assert api.keys == ["vk_add_library:2_2", "vk_add_library:3_3"]


async def test_daily_budget_pauses_with_reason_and_only_real_adds_count() -> None:
    tracks = [_track(20 + i, i, f"L{i}") for i in range(4)]
    api = FakeVkApi(catalog=_catalog(*tracks))
    guard = InMemoryWriteGuard(seconds_to_next_day=5000)
    gateway = _gateway(api, guard, library_daily_limit=2)

    with pytest.raises(PlatformRateLimitedError) as raised:
        await gateway.add_to_library([_ref(t) for t in tracks])

    assert raised.value.reason == DAILY_LIMIT_REASON
    assert raised.value.retry_after_seconds == 5000
    assert len(api.saved) == 2
    # Повтор той же пачки после паузы (новые сутки): добавленные не тратят бюджет снова.
    guard.used.clear()
    result = await gateway.add_to_library([_ref(t) for t in tracks])
    assert set(result.added) == {_ref(t) for t in tracks}
    assert len(api.saved) == 4


async def test_flood_control_backs_off_5_15_60_and_resets_after_success() -> None:
    track = _track(5, 5, "F")
    api = FakeVkApi(catalog=_catalog(track))
    guard = InMemoryWriteGuard()
    gateway = _gateway(api, guard)
    api.add_errors = [PlatformRateLimitedError(Platform.VK, 300) for _ in range(4)]

    waits = []
    for _ in range(4):
        with pytest.raises(PlatformRateLimitedError) as raised:
            await gateway.add_to_library([_ref(track)])
        assert raised.value.reason == FLOOD_REASON
        waits.append(raised.value.retry_after_seconds)
    assert waits == [300, 900, 3600, 3600]

    await gateway.add_to_library([_ref(track)])  # успех — ступень сброшена
    assert guard.flood == {}


async def test_short_rate_limit_is_not_flood() -> None:
    api = FakeVkApi(catalog=_catalog(_track(5, 5)))
    api.add_errors = [PlatformRateLimitedError(Platform.VK, 2)]
    guard = InMemoryWriteGuard()
    with pytest.raises(PlatformRateLimitedError) as raised:
        await _gateway(api, guard).add_to_library([ExternalTrackRef(Platform.VK, "5_5")])
    assert raised.value.retry_after_seconds == 2
    assert raised.value.reason is None
    assert guard.flood == {}


async def test_foreign_refs_fail_without_requests() -> None:
    api = FakeVkApi()
    result = await _gateway(api).add_to_library(
        [ExternalTrackRef(Platform.YANDEX, "1_1"), ExternalTrackRef(Platform.VK, "garbage")]
    )
    assert len(result.failed) == 2
    assert api.keys == []
