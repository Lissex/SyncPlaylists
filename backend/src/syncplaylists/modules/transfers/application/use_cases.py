from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from syncplaylists.modules.catalog.application.ports import PlatformTrackRepository
from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.modules.matching.application.ports import MatchingPipelineFactory
from syncplaylists.modules.matching.application.use_cases import ResolveTrackMatchUseCase
from syncplaylists.modules.matching.domain.pipeline import MatchStatus
from syncplaylists.modules.matching.domain.ports import TrackMatchRepository
from syncplaylists.modules.transfers.application.dto import TransferDto
from syncplaylists.modules.transfers.application.ports import TransferRepository
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.errors import InvalidTransferTransitionError
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    LibraryDestination,
    LibrarySource,
    MatchResult,
    NewPlaylist,
    PlaylistSource,
    TrackDestination,
    TrackSource,
    TransferItemStatus,
    TransferStatus,
    destination_platform,
    source_platform,
)
from syncplaylists.shared_kernel.application.ports import GatewayFactory, TaskQueue, UnitOfWork
from syncplaylists.shared_kernel.domain.ports import MusicPlatformGateway
from syncplaylists.shared_kernel.domain.search import InsertOrder, TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, PlaylistRef

_RUN_TRANSFER = "run_transfer"
_RUN_MATCH = "run_match"
_RUN_WRITE = "run_write"
_STALE_AFTER = timedelta(minutes=10)


class StartTransferUseCase:
    def __init__(
        self, uow: UnitOfWork, transfers: TransferRepository, task_queue: TaskQueue
    ) -> None:
        self._uow = uow
        self._transfers = transfers
        self._task_queue = task_queue

    async def execute(
        self, user_id: UUID, source: TrackSource, destination: TrackDestination
    ) -> TransferDto:
        transfer = Transfer(id=uuid4(), user_id=user_id, source=source, destination=destination)
        async with self._uow as uow:
            await self._transfers.save(transfer)
            await uow.commit()
        await self._task_queue.enqueue(_RUN_TRANSFER, transfer.id)
        return TransferDto.from_domain(transfer)


class ProcessTransferUseCase:
    """Таск `run_transfer`: читает источник целиком и заводит TransferItem на каждый
    трек. Постраничное чтение больших плейлистов/медиатек через `cursor` — за рамками
    этого этапа (фейковая площадка отдаёт всё за один вызов); см. долг в ARCHITECTURE.md.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        transfers: TransferRepository,
        platform_tracks: PlatformTrackRepository,
        ensure_platform_track: EnsurePlatformTrackUseCase,
        gateway_factory: GatewayFactory,
        task_queue: TaskQueue,
    ) -> None:
        self._uow = uow
        self._transfers = transfers
        self._platform_tracks = platform_tracks
        self._ensure_platform_track = ensure_platform_track
        self._gateway_factory = gateway_factory
        self._task_queue = task_queue

    async def execute(self, transfer_id: UUID) -> None:
        positions: list[int] = []
        async with self._uow as uow:
            transfer = await self._transfers.get_for_update(transfer_id)
            assert transfer is not None, f"Transfer {transfer_id} не найден"

            try:
                transfer.start(datetime.now(UTC))
            except InvalidTransferTransitionError:
                # Повторная доставка run_transfer: уже начали (или ушли дальше) раньше.
                return

            platform = source_platform(transfer.source)
            if platform is None:
                raise NotImplementedError(
                    "FileSource как источник переноса появится на этапе backups (импорт из файла)"
                )
            gateway = self._gateway_factory.for_platform(platform)
            tracks = await self._read_source(transfer.source, gateway)

            for position, candidate in enumerate(tracks):
                await self._ensure_platform_track.execute(
                    candidate.ref,
                    candidate.title,
                    candidate.artist,
                    candidate.duration,
                    candidate.isrc,
                )
                transfer.add_item(position, candidate.ref)
                positions.append(position)

            uow.track(transfer)
            await self._transfers.save(transfer)
            await uow.commit()

        for position in positions:
            await self._task_queue.enqueue(_RUN_MATCH, transfer_id, position)

    @staticmethod
    async def _read_source(
        source: TrackSource, gateway: MusicPlatformGateway
    ) -> list[TrackCandidate]:
        if isinstance(source, PlaylistSource):
            snapshot = await gateway.get_playlist(source.ref)
            return list(snapshot.tracks)
        if isinstance(source, LibrarySource):
            return [track async for track in gateway.get_library()]
        raise NotImplementedError("FileSource обрабатывается отдельно (этап backups)")


class MatchTransferItemUseCase:
    """Таск `run_match`: один трек. Вызывает matching.ResolveTrackMatchUseCase — это
    единственное место в transfers, где намеренно пересекается граница с matching, и
    только через его публичный application use case, не через matching.domain.

    Пайплайн запрашивается у MatchingPipelineFactory на каждый вызов, а не инжектится
    готовым: целевая площадка — runtime-данные конкретного Transfer
    (destination_platform), а не то, что можно зафиксировать на старте контейнера. Сам
    use case не знает, из каких стратегий/гейтвея/нормализатора/скорера пайплайн
    собирается — это внутренняя забота matching.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        transfers: TransferRepository,
        platform_tracks: PlatformTrackRepository,
        track_matches: TrackMatchRepository,
        ensure_platform_track: EnsurePlatformTrackUseCase,
        pipeline_factory: MatchingPipelineFactory,
        task_queue: TaskQueue,
    ) -> None:
        self._uow = uow
        self._transfers = transfers
        self._platform_tracks = platform_tracks
        self._track_matches = track_matches
        self._ensure_platform_track = ensure_platform_track
        self._pipeline_factory = pipeline_factory
        self._task_queue = task_queue

    async def execute(self, transfer_id: UUID, position: int) -> None:
        next_step: str | None = None
        async with self._uow as uow:
            transfer = await self._transfers.get_for_update(transfer_id)
            assert transfer is not None, f"Transfer {transfer_id} не найден"
            item = next((i for i in transfer.items if i.position == position), None)
            assert item is not None, f"TransferItem {position} не найден"

            if item.status is not TransferItemStatus.PENDING:
                return  # повторная доставка run_match — уже обработан под этим же локом

            platform_track = await self._platform_tracks.find_by_ref(item.source_track)
            assert platform_track is not None, f"platform_track для {item.source_track} не найден"
            source_candidate = TrackCandidate(
                ref=item.source_track,
                title=platform_track.raw_title,
                artist=platform_track.raw_artist,
                duration=platform_track.duration,
                isrc=platform_track.isrc,
            )

            now = datetime.now(UTC)
            target_platform = destination_platform(transfer.destination)
            pipeline = self._pipeline_factory.create(target_platform)
            resolve_track_match = ResolveTrackMatchUseCase(
                pipeline, self._track_matches, self._ensure_platform_track
            )
            attempt = await resolve_track_match.execute(source_candidate, target_platform)

            if attempt.status is MatchStatus.MATCHED and attempt.match is not None:
                result = MatchResult(
                    target_ref=attempt.match.target_ref,
                    method=attempt.match.method.value,
                    score=attempt.match.score,
                )
                transfer.record_match(position, result, now)
            elif attempt.status is MatchStatus.UNCERTAIN:
                transfer.record_uncertain(position, attempt.candidates, now)
            else:
                transfer.record_not_found(position, attempt.candidates, now)

            if not any(i.status is TransferItemStatus.PENDING for i in transfer.items):
                if transfer.has_unresolved_items():
                    transfer.enter_review()
                else:
                    transfer.begin_writing(now)
                    next_step = _RUN_WRITE

            uow.track(transfer)
            await self._transfers.save(transfer)
            await uow.commit()

        if next_step is not None:
            await self._task_queue.enqueue(next_step, transfer_id)


class ResolveUncertainItemUseCase:
    def __init__(
        self, uow: UnitOfWork, transfers: TransferRepository, task_queue: TaskQueue
    ) -> None:
        self._uow = uow
        self._transfers = transfers
        self._task_queue = task_queue

    async def execute(
        self, transfer_id: UUID, position: int, chosen_ref: ExternalTrackRef | None
    ) -> None:
        next_step: str | None = None
        async with self._uow as uow:
            transfer = await self._transfers.get_for_update(transfer_id)
            assert transfer is not None, f"Transfer {transfer_id} не найден"

            now = datetime.now(UTC)
            transfer.resolve_item(position, chosen_ref, now)
            if not transfer.has_unresolved_items():
                transfer.begin_writing(now)
                next_step = _RUN_WRITE

            uow.track(transfer)
            await self._transfers.save(transfer)
            await uow.commit()

        if next_step is not None:
            await self._task_queue.enqueue(next_step, transfer_id)


class WriteTransferUseCase:
    """Таск `run_write`. Известный долг: create_playlist на реальной площадке —
    побочный эффект вовне; если транзакция после него упадёт и откатится, повторная
    доставка run_transfer создаст плейлист повторно (нет саги/outbox). Для фейковой
    площадки на этом этапе не критично, см. ARCHITECTURE.md.
    """

    def __init__(
        self, uow: UnitOfWork, transfers: TransferRepository, gateway_factory: GatewayFactory
    ) -> None:
        self._uow = uow
        self._transfers = transfers
        self._gateway_factory = gateway_factory

    async def execute(self, transfer_id: UUID) -> None:
        async with self._uow as uow:
            transfer = await self._transfers.get_for_update(transfer_id)
            assert transfer is not None, f"Transfer {transfer_id} не найден"

            platform = destination_platform(transfer.destination)
            gateway = self._gateway_factory.for_platform(platform)
            destination = transfer.destination

            matched_items = [i for i in transfer.items if i.status is TransferItemStatus.MATCHED]
            if isinstance(destination, LibraryDestination) and (
                gateway.library_insert_order() is InsertOrder.TOP
            ):
                # Самый свежий лайк источника должен оказаться сверху и в назначении.
                matched_items = list(reversed(matched_items))
            refs = [item.match.target_ref for item in matched_items if item.match is not None]

            if isinstance(destination, LibraryDestination):
                result = await gateway.add_to_library(refs)
            else:
                playlist_ref = await self._resolve_playlist(transfer, destination, gateway)
                result = await gateway.add_tracks(playlist_ref, refs)

            failed_refs = set(result.failed)
            for item in matched_items:
                if item.match is not None and item.match.target_ref in failed_refs:
                    transfer.mark_write_failed(item.position)
                else:
                    transfer.mark_added(item.position)

            transfer.complete(datetime.now(UTC))
            uow.track(transfer)
            await self._transfers.save(transfer)
            await uow.commit()

    @staticmethod
    async def _resolve_playlist(
        transfer: Transfer, destination: TrackDestination, gateway: MusicPlatformGateway
    ) -> PlaylistRef:
        if isinstance(destination, ExistingPlaylist):
            return destination.ref
        assert isinstance(destination, NewPlaylist)
        if transfer.resolved_target is None:
            ref = await gateway.create_playlist(destination.title, destination.description)
            transfer.set_resolved_target(ref)
        assert transfer.resolved_target is not None
        return transfer.resolved_target


class GetTransferUseCase:
    def __init__(self, transfers: TransferRepository) -> None:
        self._transfers = transfers

    async def execute(self, transfer_id: UUID) -> TransferDto | None:
        transfer = await self._transfers.get(transfer_id)
        return TransferDto.from_domain(transfer) if transfer is not None else None


class SweepStaleTransfersUseCase:
    """Cron-таск: переносы в QUEUED/RUNNING, не обновлявшиеся дольше _STALE_AFTER,
    ставятся в очередь заново. Покрывает случай "джоба потерялась" (воркер упал между
    commit и enqueue, или сам ARQ-джоб потерян) — без этого такой перенос застыл бы
    навечно. Безопасно благодаря уже существующей идемпотентности: QUEUED просто
    повторяет run_transfer (ProcessTransferUseCase no-op, если уже начат кем-то
    другим); для RUNNING — только PENDING items (SELECT, без лока — мутирует не этот
    use case, а идемпотентный MatchTransferItemUseCase по каждой джобе).

    REVIEW не трогаем — это легитимное ожидание ручного решения, не "застывание".
    WRITING тоже не трогаем — не входит в объём этого этапа (см. долг в
    ARCHITECTURE.md про отсутствие transactional outbox).
    """

    def __init__(
        self,
        transfers: TransferRepository,
        task_queue: TaskQueue,
        stale_after: timedelta = _STALE_AFTER,
    ) -> None:
        self._transfers = transfers
        self._task_queue = task_queue
        self._stale_after = stale_after

    async def execute(self) -> None:
        threshold = datetime.now(UTC) - self._stale_after
        stale_ids = await self._transfers.find_stale_ids(
            (TransferStatus.QUEUED, TransferStatus.RUNNING), threshold
        )
        for transfer_id in stale_ids:
            transfer = await self._transfers.get(transfer_id)
            if transfer is None:
                continue
            if transfer.status is TransferStatus.QUEUED:
                await self._task_queue.enqueue(_RUN_TRANSFER, transfer_id)
            elif transfer.status is TransferStatus.RUNNING:
                for item in transfer.items:
                    if item.status is TransferItemStatus.PENDING:
                        await self._task_queue.enqueue(_RUN_MATCH, transfer_id, item.position)
