"""YandexGateway против настоящего API на токене из .env (YANDEX_LIVE_TOKEN).

Что меняется в аккаунте (с согласия владельца, см. план этапа 4b):
- создаётся приватный плейлист «SyncPlaylists live-test <время>» — удаляется в finally;
- ставятся лайки трекам, которых в «Мне нравится» ещё нет, — снимаются в finally.
  Трек, который уже лайкнут, тест не трогает (чтобы не снять настоящий лайк).
Если тест упал посреди — проверьте плейлисты/лайки вручную.
"""

import time
from typing import Any

import httpx
import pytest

from syncplaylists.integrations.platforms.yandex.factory import (
    YandexClientFactory,
    YandexProfileFetcher,
)
from syncplaylists.integrations.platforms.yandex.gateway import YandexGateway
from syncplaylists.integrations.platforms.yandex.ids import YandexPlaylistId
from syncplaylists.shared_kernel.application.ports import PlatformCredentials
from syncplaylists.shared_kernel.domain.errors import PlatformAuthError
from syncplaylists.shared_kernel.domain.search import TrackCandidate, TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, PlaylistRef
from tests.live.conftest import LiveSettings

pytestmark = pytest.mark.live

# Разные известные треки: их почти наверняка нет в лайках у всех сразу.
_PROBE_QUERIES = (
    TrackQuery(title="Группа крови", artist="Кино"),
    TrackQuery(title="Bohemian Rhapsody", artist="Queen"),
    TrackQuery(title="Smells Like Teen Spirit", artist="Nirvana"),
    TrackQuery(title="Billie Jean", artist="Michael Jackson"),
    TrackQuery(title="Hotel California", artist="Eagles"),
    TrackQuery(title="Wonderwall", artist="Oasis"),
)


class _Live:
    def __init__(self, gateway: YandexGateway, client: Any, uid: str) -> None:
        self.gateway = gateway
        self.client = client
        self.uid = uid


@pytest.fixture
def token(live_settings: LiveSettings) -> str:
    if live_settings.yandex_live_token is None:
        pytest.skip("YANDEX_LIVE_TOKEN не задан в .env")
    return live_settings.yandex_live_token.get_secret_value()


@pytest.fixture
async def live(token: str, live_http: httpx.AsyncClient) -> _Live:
    clients = YandexClientFactory(live_http, timeout_seconds=20)
    profile = await YandexProfileFetcher(clients).fetch(PlatformCredentials(access_token=token))
    client = clients.client(token)
    return _Live(YandexGateway(client, profile.external_user_id), client, profile.external_user_id)


async def _first_hit(gateway: YandexGateway, query: TrackQuery) -> TrackCandidate:
    results = await gateway.search(query, limit=5)
    assert results, f"поиск ничего не нашёл: {query}"
    return results[0]


async def _liked_ids(live: _Live) -> list[str]:
    likes = await live.client.users_likes_tracks(user_id=live.uid)
    return [str(short.id) for short in likes.tracks]


async def _unliked_probe_tracks(live: _Live, count: int) -> list[TrackCandidate]:
    liked = set(await _liked_ids(live))
    found: list[TrackCandidate] = []
    for query in _PROBE_QUERIES:
        track = await _first_hit(live.gateway, query)
        if track.ref.external_id.split(":")[0] not in liked:
            found.append(track)
        if len(found) == count:
            return found
    pytest.skip(f"не нашлось {count} пробных треков без лайка — лайки не трогаем")


# --- чтение ---------------------------------------------------------------------------


async def test_profile_and_rejected_token(token: str, live_http: httpx.AsyncClient) -> None:
    fetcher = YandexProfileFetcher(YandexClientFactory(live_http, timeout_seconds=20))

    profile = await fetcher.fetch(PlatformCredentials(access_token=token))
    assert profile.external_user_id.isdigit()

    # Проверка допущения транспорта: протухший/чужой токен Яндекс отдаёт как 401
    # (→ PlatformAuthError → EXPIRED), а не 403.
    with pytest.raises(PlatformAuthError):
        await fetcher.fetch(PlatformCredentials(access_token="y0_definitely-not-a-token"))


async def test_library_first_tracks(live: _Live) -> None:
    tracks: list[TrackCandidate] = []
    async for track in live.gateway.get_library():
        tracks.append(track)
        if len(tracks) == 5:
            break

    for track in tracks:
        assert track.title
        assert track.artist
        assert track.duration is not None


async def test_search_maps_versions_and_covers(live: _Live) -> None:
    results = await live.gateway.search(TrackQuery(title="Группа крови", artist="Кино"))

    assert any("Группа крови" in r.title for r in results)
    assert any(r.cover_url and r.cover_url.startswith("https://") for r in results)


async def test_own_likes_link_is_recognized(live: _Live) -> None:
    status = await live.client.account_status()
    login = status.account.login

    assert await live.gateway.is_own_library(PlaylistRef(live.gateway.platform, f"{login}:3"))
    assert await live.gateway.is_own_library(PlaylistRef(live.gateway.platform, f"{live.uid}:3"))


# --- запись: временный плейлист -------------------------------------------------------


async def test_playlist_create_add_order_and_no_duplicates(live: _Live) -> None:
    first = await _first_hit(live.gateway, _PROBE_QUERIES[0])
    second = await _first_hit(live.gateway, _PROBE_QUERIES[1])
    ref = await live.gateway.create_playlist(
        f"SyncPlaylists live-test {int(time.time())}", "Создан тестом, будет удалён"
    )
    try:
        info = await live.gateway.playlist_info(ref)
        assert info.owner_external_id == live.uid

        added = await live.gateway.add_tracks(ref, [first.ref, second.ref])
        again = await live.gateway.add_tracks(ref, [second.ref, first.ref])  # повтор run_write

        assert added.failed == ()
        assert again.failed == ()
        snapshot = await live.gateway.get_playlist(ref)
        ids = [t.ref.external_id.split(":")[0] for t in snapshot.tracks]
        assert ids == [first.ref.external_id.split(":")[0], second.ref.external_id.split(":")[0]]
    finally:
        kind = YandexPlaylistId.parse(ref.external_id).kind
        await live.client.users_playlists_delete(kind, user_id=live.uid)


# --- запись: лайки (снимаются в finally) ----------------------------------------------


def _verdict(sent: list[str], top: list[str]) -> str:
    if top == sent:
        return "прямой (первый в пачке — сверху)"
    if top == list(reversed(sent)):
        return "обратный (последний в пачке — сверху)"
    if top == sorted(sent, key=int, reverse=True):
        return "по id убыванием"
    if top == sorted(sent, key=int):
        return "по id возрастанием"
    return "не определён"


async def test_batch_like_order_is_reported(live: _Live) -> None:
    """Как users_likes_tracks_add раскладывает пачку в «Мне нравится». Две пачки из одних
    и тех же треков в разном, немонотонном по id порядке — чтобы отличить «сохраняет
    порядок пачки» от «сортирует по id». Результат печатается (запуск с -s) и
    записывается в ARCHITECTURE.md (11d); от него зависит library_batch_preserves_order."""
    probes = await _unliked_probe_tracks(live, 3)
    by_id = sorted(probes, key=lambda p: int(p.ref.external_id.split(":")[0]))
    rounds = ([by_id[1], by_id[2], by_id[0]], [by_id[0], by_id[2], by_id[1]])  # не монотонно
    verdicts: list[str] = []
    for order in rounds:
        ids = [p.ref.external_id for p in order]
        sent = [i.split(":")[0] for i in ids]
        try:
            await live.client.users_likes_tracks_add(ids, user_id=live.uid)
            top = (await _liked_ids(live))[:3]
        finally:
            await live.client.users_likes_tracks_remove(ids, user_id=live.uid)
        verdicts.append(_verdict(sent, top))
        print(f"\nОтправлено пачкой: {sent}")
        print(f"Верх «Мне нравится»: {top}")
        print(f"Порядок: {verdicts[-1]}")
        assert set(top) == set(sent)
    if len(set(verdicts)) == 1 and verdicts[0].startswith(("прямой", "обратный")):
        print(f"ИТОГ: пачка сохраняет порядок, {verdicts[0]}")
    else:
        print(f"ИТОГ: порядок внутри пачки не гарантирован ({verdicts})")


async def test_add_to_library_keeps_order(live: _Live) -> None:
    # Пачка из 3 треков, отправленная развёрнутой (см. YandexGateway.add_to_library).
    probes = await _unliked_probe_tracks(live, 3)
    refs: list[ExternalTrackRef] = [p.ref for p in probes]
    try:
        result = await live.gateway.add_to_library(refs)
        assert result.failed == ()
        top = (await _liked_ids(live))[:3]
        # Последний добавленный — сверху: WriteTransferUseCase поэтому разворачивает
        # список для InsertOrder.TOP.
        assert top == [r.external_id.split(":")[0] for r in reversed(refs)]
    finally:
        await live.client.users_likes_tracks_remove([r.external_id for r in refs], user_id=live.uid)
