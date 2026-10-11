import hashlib
import logging
from collections.abc import AsyncIterator, Awaitable, Sequence
from dataclasses import dataclass
from typing import Final, TypeVar
from uuid import UUID

from syncplaylists.integrations.platforms.vk.api import VkApi, VkBadRequestError
from syncplaylists.integrations.platforms.vk.extension_wire import VkPlaylist
from syncplaylists.integrations.platforms.vk.ids import VkPlaylistId, VkTrackId
from syncplaylists.integrations.platforms.vk.mapping import identity, to_candidate
from syncplaylists.shared_kernel.application.ports import PlatformWriteGuard
from syncplaylists.shared_kernel.domain.errors import (
    PlatformRateLimitedError,
    PlaylistNotFoundError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.search import (
    AddResult,
    InsertOrder,
    PlaylistInfo,
    PlaylistSnapshot,
    TrackCandidate,
    TrackQuery,
)
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    ExternalTrackRef,
    Platform,
    PlaylistRef,
)

logger = logging.getLogger(__name__)

_PLATFORM: Final = Platform.VK
_T = TypeVar("_T")
# Причины паузы переноса (PAUSED_QUOTA) — их видит пользователь.
DAILY_LIMIT_REASON: Final = "vk_library_daily_limit"
FLOOD_REASON: Final = "vk_flood_control"
_LIBRARY_ADD: Final = "library_add"
# retry_after от страницы не меньше этого — VK ответил «flood control» (error 9), а не
# «слишком часто» (error 6, секунды).
_FLOOD_MIN_SECONDS: Final = 60.0


@dataclass(frozen=True, slots=True)
class VkLimits:
    # Не больше стольких добавлений в «Мою музыку» за сутки (по Москве) на аккаунт.
    library_daily_limit: int = 300
    # Ступени паузы после «flood control»: 5 → 15 → 60 минут, дальше по 60.
    flood_backoff_seconds: tuple[float, ...] = (300.0, 900.0, 3600.0)


@dataclass(frozen=True, slots=True)
class _Pending:
    ref: ExternalTrackRef
    track_id: VkTrackId
    keys: frozenset[str]


class VkGateway:
    """MusicPlatformGateway для VK Музыки — только через браузерное расширение (4c-5).

    Особенности VK (спайк 4c-5-0, ARCHITECTURE.md 11h):
    - ISRC нет — search_by_isrc пуст, матчинг идёт по тексту; официальный релиз
      отличается по main_artists + release_audio_id (rights_holder);
    - в плейлист и «Мою музыку» VK кладёт копии треков с новыми id и дубли не отсеивает —
      уже добавленное узнаём по mapping.identity, а не по id;
    - add/createPlaylist ставят каждый трек наверх, поэтому плейлист пишется заменой
      списка целиком (editPlaylist): порядок точный, повтор идемпотентен;
    - «Моя музыка» — новое сверху (InsertOrder.TOP), по одному треку с паузой 2–3 с
      (её держит расширение), не больше суточного бюджета аккаунта.
    """

    platform = _PLATFORM

    def __init__(
        self,
        api: VkApi,
        own_id: str,
        account_id: UUID,
        guard: PlatformWriteGuard,
        limits: VkLimits | None = None,
    ) -> None:
        self._api = api
        self._own_id = own_id
        self._account_id = account_id
        self._guard = guard
        self._limits = limits or VkLimits()

    # --- чтение -----------------------------------------------------------------------

    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]:
        text = f"{query.artist} {query.title}" if query.artist else query.title
        found = await self._api.search(text, max(limit * 2, 10))
        # Свои копии из «Моей музыки» — не кандидаты: это тот же трек под другим id.
        candidates = [to_candidate(t) for t in found if str(t.owner_id) != self._own_id]
        return [c for c in candidates if c is not None][:limit]

    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]:
        return []  # ISRC у VK нет — пусть работает FuzzySearchStrategy

    async def playlist_info(self, ref: PlaylistRef) -> PlaylistInfo:
        playlist = await self._api.playlist_info(_parse_playlist(ref))
        return PlaylistInfo(
            ref=ref,
            title=playlist.title,
            description=playlist.description or None,
            owner_external_id=str(playlist.owner_id),
            track_count=playlist.count,
        )

    async def get_playlist(self, ref: PlaylistRef) -> PlaylistSnapshot:
        playlist, tracks = await self._api.playlist(_parse_playlist(ref))
        candidates = [c for c in (to_candidate(t) for t in tracks) if c is not None]
        return PlaylistSnapshot(
            ref=ref,
            title=playlist.title,
            description=playlist.description or None,
            tracks=tuple(candidates),
        )

    async def get_library(self) -> AsyncIterator[TrackCandidate]:
        # Порядок — как у VK: сверху последнее добавленное.
        async for track in self._api.library():
            candidate = to_candidate(track)
            if candidate is not None:
                yield candidate

    async def is_own_library(self, ref: PlaylistRef) -> bool:
        return False  # «Моя музыка» ссылкой-плейлистом не задаётся (только источник library)

    def library_insert_order(self) -> InsertOrder:
        return InsertOrder.TOP

    def playlist_capacity(self) -> int | None:
        return None  # лимит VK неизвестен (ARCHITECTURE.md 11h, долги 4c-5)

    # --- запись -----------------------------------------------------------------------

    async def create_playlist(
        self, title: str, description: str | None, *, request_id: str | None = None
    ) -> PlaylistRef:
        created = await self._write(
            self._api.create_playlist(
                title,
                description or "",
                idempotency_key=f"create_playlist:{request_id}" if request_id else None,
            )
        )
        playlist_id = VkPlaylistId(created.owner_id, created.id, created.access_key)
        return PlaylistRef(_PLATFORM, str(playlist_id))

    async def add_tracks(
        self, playlist: PlaylistRef, tracks: Sequence[ExternalTrackRef]
    ) -> AddResult:
        playlist_id = _parse_playlist(playlist)
        header, present = await self._api.playlist(playlist_id)
        self._ensure_writable(header)
        added: list[ExternalTrackRef] = []
        failed: list[ExternalTrackRef] = []
        known = {key for track in present for key in identity(track)}
        new = await self._new_tracks(tracks, known, added, failed)
        if new:
            ok, bad = await self._replace(header.id, [t.plain for t in present], new)
            added.extend(ok)
            failed.extend(bad)
        return AddResult(added=tuple(added), failed=tuple(failed))

    async def add_to_library(self, tracks: Sequence[ExternalTrackRef]) -> AddResult:
        added: list[ExternalTrackRef] = []
        failed: list[ExternalTrackRef] = []
        library = await self._api.library_snapshot()
        known = {key for track in library for key in identity(track)}
        # По одному, в порядке списка (WriteTransferUseCase уже развернул его под
        # InsertOrder.TOP — последний трек окажется сверху). Если бюджет суток кончится
        # посреди пачки — пауза переноса; добавленное до неё при повторе отсеется по
        # медиатеке.
        for pending in await self._new_tracks(tracks, known, added, failed):
            wait = await self._guard.take_daily(
                _PLATFORM, self._account_id, _LIBRARY_ADD, self._limits.library_daily_limit
            )
            if wait is not None:
                raise PlatformRateLimitedError(
                    _PLATFORM,
                    max(wait, _FLOOD_MIN_SECONDS),
                    "дневной лимит добавлений в «Мою музыку»",
                    reason=DAILY_LIMIT_REASON,
                )
            try:
                await self._write(
                    self._api.add_to_library(
                        pending.track_id,
                        idempotency_key=f"vk_add_library:{pending.track_id.plain}",
                    )
                )
            except (VkBadRequestError, PlaylistNotFoundError, PlaylistNotWritableError):
                logger.warning("VK не добавил трек %s в «Мою музыку»", pending.track_id.plain)
                failed.append(pending.ref)
            else:
                added.append(pending.ref)
        return AddResult(added=tuple(added), failed=tuple(failed))

    # --- внутреннее -------------------------------------------------------------------

    def _ensure_writable(self, playlist: VkPlaylist) -> None:
        if str(playlist.owner_id) != self._own_id:
            raise PlaylistNotWritableError(_PLATFORM, "плейлист другого аккаунта")
        # Альбом и сгенерированный VK плейлист («Открытия») не редактируются. Свой обычный
        # (ugc) — всегда: только что созданный плейлист VK может отдать без permissions.edit.
        if not playlist.editable and playlist.type != "ugc":
            logger.warning("VK: плейлист %s не редактируется (type=%s)", playlist.id, playlist.type)
            raise PlaylistNotWritableError(
                _PLATFORM, f"плейлист VK не редактируется ({playlist.type})"
            )

    async def _new_tracks(
        self,
        tracks: Sequence[ExternalTrackRef],
        known: set[str],
        added: list[ExternalTrackRef],
        failed: list[ExternalTrackRef],
    ) -> list[_Pending]:
        """Отсеивает то, что уже есть в назначении (считается добавленным — повтор
        run_write после сбоя не задвоит треки), дубли внутри запроса и чужие id. Детали
        новых треков (оригинал релиза, текст) нужны, чтобы узнать их копии."""
        parsed: list[tuple[ExternalTrackRef, VkTrackId]] = []
        for ref in tracks:
            try:
                track_id = VkTrackId.parse(ref.external_id)
            except ValueError:
                failed.append(ref)
                continue
            if ref.platform is not _PLATFORM:
                failed.append(ref)
            else:
                parsed.append((ref, track_id))
        details = {t.plain: t for t in await self._api.tracks([tid for _, tid in parsed])}

        seen = set(known)
        new: list[_Pending] = []
        for ref, track_id in parsed:
            track = details.get(track_id.plain)
            keys = frozenset(identity(track) if track else {f"id:{track_id.plain}"})
            if keys & seen:
                added.append(ref)
                continue
            seen |= keys
            new.append(_Pending(ref, track_id, keys))
        return new

    async def _replace(
        self, playlist_id: int, base: list[str], new: list[_Pending]
    ) -> tuple[list[ExternalTrackRef], list[ExternalTrackRef]]:
        """Записать base + new одной заменой списка. VK не принял (bad_request) — делим
        new пополам, пока не найдём трек, который он не берёт: тот → failed, остальные
        пишутся. Каждая попытка — снова замена целиком, так что неудачная ничего не
        портит."""
        audio_ids = [*base, *(p.track_id.plain for p in new)]
        try:
            await self._write(
                self._api.set_playlist_tracks(
                    playlist_id, audio_ids, idempotency_key=_list_key(playlist_id, audio_ids)
                )
            )
        except VkBadRequestError:
            if len(new) == 1:
                logger.warning("VK не принял трек %s в плейлист", new[0].track_id.plain)
                return [], [new[0].ref]
            middle = len(new) // 2
            left_ok, left_bad = await self._replace(playlist_id, base, new[:middle])
            accepted = [p.track_id.plain for p in new[:middle] if p.ref in left_ok]
            right_ok, right_bad = await self._replace(playlist_id, [*base, *accepted], new[middle:])
            return [*left_ok, *right_ok], [*left_bad, *right_bad]
        return [p.ref for p in new], []

    async def _write(self, operation: Awaitable[_T]) -> _T:
        """Запись на площадку: «flood control» VK → пауза переноса нарастающей длины
        (5 → 15 → 60 мин), успешная запись сбрасывает ступень."""
        try:
            result = await operation
        except PlatformRateLimitedError as exc:
            if exc.retry_after_seconds < _FLOOD_MIN_SECONDS:
                raise
            seconds = await self._guard.flood_penalty(
                _PLATFORM, self._account_id, self._limits.flood_backoff_seconds
            )
            raise PlatformRateLimitedError(
                _PLATFORM, seconds, "flood control VK", reason=FLOOD_REASON
            ) from exc
        await self._guard.clear_flood(_PLATFORM, self._account_id)
        return result


def _parse_playlist(ref: PlaylistRef) -> VkPlaylistId:
    try:
        return VkPlaylistId.parse(ref.external_id)
    except ValueError as exc:
        raise PlaylistNotFoundError(_PLATFORM, str(exc)) from exc


def _list_key(playlist_id: int, audio_ids: Sequence[str]) -> str:
    digest = hashlib.sha256(",".join(audio_ids).encode()).hexdigest()[:32]
    return f"vk_set_tracks:{playlist_id}:{digest}"
