from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from syncplaylists.modules.catalog.application.ports import PlatformTrackRepository
from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.modules.matching.application.ports import MatchingPipelineFactory
from syncplaylists.modules.matching.application.use_cases import ResolveTrackMatchUseCase
from syncplaylists.modules.matching.domain.pipeline import MatchStatus
from syncplaylists.modules.matching.domain.ports import TrackMatchRepository
from syncplaylists.modules.transfers.application.dto import (
    TransferDto,
    TransferProgressDto,
)
from syncplaylists.modules.transfers.application.ports import TransferRepository
from syncplaylists.modules.transfers.domain.entities import Transfer, TransferItem
from syncplaylists.modules.transfers.domain.errors import InvalidTransferTransitionError
from syncplaylists.modules.transfers.domain.progress import ETA_WINDOW
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
from syncplaylists.shared_kernel.application.ports import (
    AccountAccess,
    AccountAccessProvider,
    AccountNotAvailableError,
    GatewayFactory,
    TaskQueue,
    UnitOfWork,
)
from syncplaylists.shared_kernel.domain.errors import (
    PlatformAuthError,
    PlatformError,
    PlatformNotSupportedError,
    PlatformRegionError,
    PlaylistNotFoundError,
    PlaylistNotWritableError,
)
from syncplaylists.shared_kernel.domain.ports import MusicPlatformGateway
from syncplaylists.shared_kernel.domain.search import InsertOrder, TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform, PlaylistRef

_RUN_TRANSFER = "run_transfer"
_RUN_MATCH = "run_match"
_RUN_WRITE = "run_write"
_STALE_AFTER = timedelta(minutes=10)
_ACCOUNT_UNAVAILABLE = "account_unavailable"
_ACCOUNT_EXPIRED = "account_expired"
_PLATFORM_NOT_SUPPORTED = "platform_not_supported"


class TransferNotFoundError(Exception):
    """Переноса нет или он принадлежит другому пользователю (не различаем намеренно)."""


async def _access_for(
    accounts: AccountAccessProvider, user_id: UUID, platform: Platform, account_id: UUID | None
) -> AccountAccess:
    """Доступ к площадке от имени пользователя: явный account_id (медиатека) — этот
    аккаунт, иначе единственный активный аккаунт пользователя на площадке
    (ARCHITECTURE.md, 11b). Бросает AccountNotAvailableError."""
    if account_id is None:
        return await accounts.for_platform(user_id, platform)
    access = await accounts.get(user_id, account_id)
    if access.platform is not platform:
        raise AccountNotAvailableError(f"Аккаунт {account_id} подключён не к {platform}")
    return access


async def _source_access(accounts: AccountAccessProvider, transfer: Transfer) -> AccountAccess:
    source = transfer.source
    platform = source_platform(source)
    if platform is None:
        raise NotImplementedError(
            "FileSource как источник переноса появится на этапе backups (импорт из файла)"
        )
    account_id = source.account_id if isinstance(source, LibrarySource) else None
    return await _access_for(accounts, transfer.user_id, platform, account_id)


async def _destination_access(accounts: AccountAccessProvider, transfer: Transfer) -> AccountAccess:
    destination = transfer.destination
    account_id = destination.account_id if isinstance(destination, LibraryDestination) else None
    return await _access_for(
        accounts, transfer.user_id, destination_platform(destination), account_id
    )


async def _fail_running_transfer(
    uow: UnitOfWork, transfers: TransferRepository, transfer: Transfer, reason: str
) -> None:
    """Для «шапки» переноса (run_match): условный переход RUNNING → FAILED в БД, без
    полного сохранения агрегата, которое перетёрло бы items параллельных джоб."""
    if await transfers.transition_status(
        transfer.id, TransferStatus.RUNNING, TransferStatus.FAILED
    ):
        transfer.fail(reason, datetime.now(UTC))
        uow.track(transfer)
    await uow.commit()


async def _fail_transfer(
    uow: UnitOfWork,
    transfers: TransferRepository,
    transfer: Transfer,
    reason: str = _ACCOUNT_UNAVAILABLE,
) -> None:
    """Аккаунт отключили/он истёк, плейлист пропал и т.п. посреди переноса: перенос
    FAILED (с событием для SSE), без исключения из ARQ-таска — повтор джобы не поможет.
    Только вне фазы RUNNING: полный save перетёр бы items параллельных run_match."""
    transfer.fail(reason, datetime.now(UTC))
    uow.track(transfer)
    await transfers.save(transfer)
    await uow.commit()


async def _terminal_failure_reason(
    accounts: AccountAccessProvider, access: AccountAccess, exc: PlatformError
) -> str | None:
    """Ошибка площадки → причина FAILED переноса, если повтор не поможет; None — ошибка
    временная (сеть, rate limit, разовый 401), исключение нужно пробросить в retry."""
    if isinstance(exc, PlatformAuthError):
        # 401 ещё не значит «токен протух» — accounts один раз перепроверит его через
        # профиль и только тогда переведёт аккаунт в EXPIRED.
        expired = await accounts.report_auth_failure(access.account_id)
        return _ACCOUNT_EXPIRED if expired else None
    if isinstance(exc, PlaylistNotFoundError):
        return "playlist_not_found"
    if isinstance(exc, PlaylistNotWritableError):
        return "playlist_not_writable"
    if isinstance(exc, PlatformRegionError):
        return "region_blocked"
    return None


class StartTransferUseCase:
    def __init__(
        self,
        uow: UnitOfWork,
        transfers: TransferRepository,
        task_queue: TaskQueue,
        accounts: AccountAccessProvider,
        gateway_factory: GatewayFactory,
    ) -> None:
        self._uow = uow
        self._transfers = transfers
        self._task_queue = task_queue
        self._accounts = accounts
        self._gateway_factory = gateway_factory

    async def execute(
        self, user_id: UUID, source: TrackSource, destination: TrackDestination
    ) -> TransferDto:
        """Бросает AccountNotAvailableError, PlatformNotSupportedError,
        PlaylistNotWritableError/PlaylistNotFoundError (назначение — чужой или
        несуществующий плейлист) и прочие PlatformError."""
        transfer = Transfer(id=uuid4(), user_id=user_id, source=source, destination=destination)
        for platform in (source_platform(source), destination_platform(destination)):
            if platform is not None and not self._gateway_factory.supports(platform):
                raise PlatformNotSupportedError(platform)
        # Ранняя проверка: аккаунты источника и назначения есть, свои и активны — иначе
        # AccountNotAvailableError до постановки в очередь, а не FAILED уже в воркере.
        # Сами токены дальше не передаются: воркер резолвит доступ заново по account_id.
        await _source_access(self._accounts, transfer)
        target_access = await _destination_access(self._accounts, transfer)
        if isinstance(destination, ExistingPlaylist):
            await self._ensure_writable(target_access, destination.ref)
        async with self._uow as uow:
            await self._transfers.save(transfer)
            await uow.commit()
        await self._task_queue.enqueue(_RUN_TRANSFER, transfer.id)
        return TransferDto.from_domain(transfer)

    async def _ensure_writable(self, access: AccountAccess, ref: PlaylistRef) -> None:
        # Писать можно только в свой плейлист — проверяем заранее, а не узнаём по 403
        # после часа матчинга.
        info = await self._gateway_factory.for_account(access).playlist_info(ref)
        if info.owner_external_id != access.external_user_id:
            raise PlaylistNotWritableError(ref.platform, "плейлист принадлежит другому аккаунту")


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
        accounts: AccountAccessProvider,
    ) -> None:
        self._uow = uow
        self._transfers = transfers
        self._platform_tracks = platform_tracks
        self._ensure_platform_track = ensure_platform_track
        self._gateway_factory = gateway_factory
        self._task_queue = task_queue
        self._accounts = accounts

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

            try:
                access = await _source_access(self._accounts, transfer)
            except AccountNotAvailableError:
                await _fail_transfer(uow, self._transfers, transfer)
                return
            try:
                gateway = self._gateway_factory.for_account(access)
                tracks = await self._read_source(transfer.source, gateway)
            except PlatformNotSupportedError:
                await _fail_transfer(uow, self._transfers, transfer, _PLATFORM_NOT_SUPPORTED)
                return
            except PlatformError as exc:
                reason = await _terminal_failure_reason(self._accounts, access, exc)
                if reason is None:
                    raise  # временная ошибка — повтор run_transfer (tasks.py)
                await _fail_transfer(uow, self._transfers, transfer, reason)
                return

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

    Конкурентность: по одному переносу параллельно идут сотни run_match. Агрегат целиком
    здесь НЕ загружается и НЕ сохраняется (иначе либо глобальный лок на перенос на всё
    время сетевого матчинга, либо потерянные обновления при merge). Вместо этого:
    «шапка» переноса + один item, результат пишется условным UPDATE одного item, а
    счётчики transfers — атомарным `SET x = x + 1` (TransferRepository.save_item_outcome).
    Переход RUNNING → REVIEW/WRITING делает ровно та джоба, чей декремент дал pending == 0.

    Пайплайн запрашивается у MatchingPipelineFactory на каждый вызов: целевая площадка
    и аккаунт — runtime-данные конкретного Transfer.
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
        accounts: AccountAccessProvider,
    ) -> None:
        self._uow = uow
        self._transfers = transfers
        self._platform_tracks = platform_tracks
        self._track_matches = track_matches
        self._ensure_platform_track = ensure_platform_track
        self._pipeline_factory = pipeline_factory
        self._task_queue = task_queue
        self._accounts = accounts

    async def execute(self, transfer_id: UUID, position: int) -> None:
        next_step: str | None = None
        async with self._uow as uow:
            loaded = await _load_pending_item(self._transfers, transfer_id, position)
            if loaded is None:
                return  # повторная доставка / перенос уже не RUNNING
            transfer, item = loaded

            try:
                target_access = await _destination_access(self._accounts, transfer)
            except AccountNotAvailableError:
                await _fail_running_transfer(uow, self._transfers, transfer, _ACCOUNT_UNAVAILABLE)
                return

            platform_track = await self._platform_tracks.find_by_ref(item.source_track)
            assert platform_track is not None, f"platform_track для {item.source_track} не найден"
            source_candidate = TrackCandidate(
                ref=item.source_track,
                title=platform_track.raw_title,
                artist=platform_track.raw_artist,
                duration=platform_track.duration,
                isrc=platform_track.isrc,
            )

            target_platform = destination_platform(transfer.destination)
            try:
                pipeline = self._pipeline_factory.create(target_access)
                resolve_track_match = ResolveTrackMatchUseCase(
                    pipeline, self._track_matches, self._ensure_platform_track
                )
                attempt = await resolve_track_match.execute(source_candidate, target_platform)
            except PlatformNotSupportedError:
                await _fail_running_transfer(
                    uow, self._transfers, transfer, _PLATFORM_NOT_SUPPORTED
                )
                return
            except PlatformError as exc:
                reason = await _terminal_failure_reason(self._accounts, target_access, exc)
                if reason is None:
                    raise  # временная ошибка — повтор run_match (tasks.py)
                await _fail_running_transfer(uow, self._transfers, transfer, reason)
                return

            if attempt.status is MatchStatus.MATCHED and attempt.match is not None:
                item.apply_match(
                    MatchResult(
                        target_ref=attempt.match.target_ref,
                        method=attempt.match.method.value,
                        score=attempt.match.score,
                    )
                )
            elif attempt.status is MatchStatus.UNCERTAIN:
                item.apply_uncertain(attempt.candidates)
            else:
                item.apply_not_found(attempt.candidates)

            next_step = await _commit_item_outcome(uow, self._transfers, transfer, item)

        if next_step is not None:
            await self._task_queue.enqueue(next_step, transfer_id)


class FailTransferItemUseCase:
    """run_match исчерпал повторы: item → FAILED, перенос продолжает остальные треки
    (и сам переходит в REVIEW/WRITING, если этот item был последним) — вместо того чтобы
    навсегда остаться в RUNNING с вечным PENDING."""

    def __init__(
        self, uow: UnitOfWork, transfers: TransferRepository, task_queue: TaskQueue
    ) -> None:
        self._uow = uow
        self._transfers = transfers
        self._task_queue = task_queue

    async def execute(self, transfer_id: UUID, position: int, reason: str) -> None:
        next_step: str | None = None
        async with self._uow as uow:
            loaded = await _load_pending_item(self._transfers, transfer_id, position)
            if loaded is None:
                return
            transfer, item = loaded
            item.apply_processing_failure()
            next_step = await _commit_item_outcome(
                uow, self._transfers, transfer, item, failure_reason=reason
            )

        if next_step is not None:
            await self._task_queue.enqueue(next_step, transfer_id)


class FailTransferUseCase:
    """run_transfer/run_write исчерпали повторы на временной ошибке площадки: перенос
    FAILED с причиной — вместо вечного QUEUED (sweeper гонял бы его по кругу) или
    WRITING (sweeper его не трогает)."""

    def __init__(self, uow: UnitOfWork, transfers: TransferRepository) -> None:
        self._uow = uow
        self._transfers = transfers

    async def execute(self, transfer_id: UUID, reason: str) -> None:
        async with self._uow as uow:
            transfer = await self._transfers.get_for_update(transfer_id)
            if transfer is None or transfer.status in (TransferStatus.DONE, TransferStatus.FAILED):
                return
            if transfer.status is TransferStatus.RUNNING:
                # Идут run_match — только условный переход «шапки», без полного save.
                await _fail_running_transfer(uow, self._transfers, transfer, reason)
                return
            await _fail_transfer(uow, self._transfers, transfer, reason)


async def _load_pending_item(
    transfers: TransferRepository, transfer_id: UUID, position: int
) -> tuple[Transfer, TransferItem] | None:
    transfer = await transfers.get_header(transfer_id)
    assert transfer is not None, f"Transfer {transfer_id} не найден"
    if transfer.status is not TransferStatus.RUNNING:
        return None  # перенос уже упал/ушёл дальше — items не трогаем
    item = await transfers.get_item(transfer_id, position)
    assert item is not None, f"TransferItem {position} не найден"
    if item.status is not TransferItemStatus.PENDING:
        return None  # повторная доставка — уже обработан
    return transfer, item


async def _commit_item_outcome(
    uow: UnitOfWork,
    transfers: TransferRepository,
    transfer: Transfer,
    item: TransferItem,
    failure_reason: str = "",
) -> str | None:
    """Сохраняет результат одного item точечно и, если он последний, переводит перенос
    дальше. Возвращает имя следующей задачи (run_write) или None."""
    progress = await transfers.save_item_outcome(item)
    if progress is None:
        # Параллельная доставка той же джобы успела раньше (её коммит мы дождались на
        # условном UPDATE) — наш результат не нужен, побочные записи откатываем.
        await uow.rollback()
        return None

    now = datetime.now(UTC)
    transfer.record_item_outcome(item, now, failure_reason)
    next_step: str | None = None
    next_status = progress.status_after_matching()
    if next_status is not None and await transfers.transition_status(
        transfer.id, TransferStatus.RUNNING, next_status
    ):
        # Порядок: сначала условный переход в БД, потом доменный метод (события) —
        # если перенос параллельно упал, событий о переходе не будет.
        transfer.finish_matching(progress, now)
        if next_status is TransferStatus.WRITING:
            next_step = _RUN_WRITE
    uow.track(transfer)
    await uow.commit()
    return next_step


class ResolveUncertainItemUseCase:
    def __init__(
        self, uow: UnitOfWork, transfers: TransferRepository, task_queue: TaskQueue
    ) -> None:
        self._uow = uow
        self._transfers = transfers
        self._task_queue = task_queue

    async def execute(
        self,
        user_id: UUID,
        transfer_id: UUID,
        position: int,
        chosen_ref: ExternalTrackRef | None,
    ) -> None:
        next_step: str | None = None
        async with self._uow as uow:
            transfer = await self._transfers.get_for_update(transfer_id)
            if transfer is None or transfer.user_id != user_id:
                raise TransferNotFoundError(str(transfer_id))

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
    """Таск `run_write`.

    Создание нового плейлиста — побочный эффект вовне, который откатом транзакции не
    отменить. Поэтому это отдельный шаг: создали → сразу закоммитили resolved_target →
    снова взяли лок на перенос и проверили статус. Если запись треков потом упадёт и
    run_write повторится, он переиспользует уже созданный плейлист, а не создаст второй.
    Остаётся только окно между ответом площадки на create и нашим коммитом (сбой
    процесса ровно в этот момент) — см. ARCHITECTURE.md, 11d.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        transfers: TransferRepository,
        gateway_factory: GatewayFactory,
        accounts: AccountAccessProvider,
    ) -> None:
        self._uow = uow
        self._transfers = transfers
        self._gateway_factory = gateway_factory
        self._accounts = accounts

    async def execute(self, transfer_id: UUID) -> None:
        async with self._uow as uow:
            transfer = await self._transfers.get_for_update(transfer_id)
            assert transfer is not None, f"Transfer {transfer_id} не найден"
            if transfer.status is not TransferStatus.WRITING:
                return  # повторная доставка run_write после complete()/fail()

            try:
                access = await _destination_access(self._accounts, transfer)
            except AccountNotAvailableError:
                await _fail_transfer(uow, self._transfers, transfer)
                return
            try:
                if self._needs_new_playlist(transfer):
                    await self._create_playlist(transfer, access)
                    await self._transfers.save(transfer)
                    await uow.commit()  # resolved_target зафиксирован до записи треков
                    # Коммит снял лок — берём снова: параллельная доставка run_write могла
                    # успеть дописать перенос, пока лока не было.
                    reloaded = await self._transfers.get_for_update(transfer_id)
                    assert reloaded is not None
                    if reloaded.status is not TransferStatus.WRITING:
                        return
                    transfer = reloaded
                await self._write(transfer, access)
            except PlatformNotSupportedError:
                await _fail_transfer(uow, self._transfers, transfer, _PLATFORM_NOT_SUPPORTED)
                return
            except PlatformError as exc:
                reason = await _terminal_failure_reason(self._accounts, access, exc)
                if reason is None:
                    # Временная ошибка — повтор run_write. Уже добавленное повтор не
                    # задвоит: адаптеры вычитают треки, которые уже есть в назначении.
                    raise
                await _fail_transfer(uow, self._transfers, transfer, reason)
                return

            transfer.complete(datetime.now(UTC))
            uow.track(transfer)
            await self._transfers.save(transfer)
            await uow.commit()

    async def _write(self, transfer: Transfer, access: AccountAccess) -> None:
        gateway = self._gateway_factory.for_account(access)
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

    @staticmethod
    def _needs_new_playlist(transfer: Transfer) -> bool:
        return isinstance(transfer.destination, NewPlaylist) and transfer.resolved_target is None

    async def _create_playlist(self, transfer: Transfer, access: AccountAccess) -> None:
        destination = transfer.destination
        assert isinstance(destination, NewPlaylist)
        gateway = self._gateway_factory.for_account(access)
        ref = await gateway.create_playlist(destination.title, destination.description)
        transfer.set_resolved_target(ref)

    @staticmethod
    async def _resolve_playlist(
        transfer: Transfer, destination: TrackDestination, gateway: MusicPlatformGateway
    ) -> PlaylistRef:
        if isinstance(destination, ExistingPlaylist):
            return destination.ref
        # NewPlaylist: плейлист создан и закоммичен отдельным шагом в execute().
        assert transfer.resolved_target is not None
        return transfer.resolved_target


class GetTransferUseCase:
    def __init__(self, transfers: TransferRepository) -> None:
        self._transfers = transfers

    async def execute(self, user_id: UUID, transfer_id: UUID) -> TransferDto | None:
        transfer = await self._transfers.get(transfer_id)
        if transfer is None or transfer.user_id != user_id:
            return None
        sample = await self._transfers.progress_sample(transfer_id, ETA_WINDOW)
        progress = TransferProgressDto.from_sample(sample, datetime.now(UTC)) if sample else None
        return TransferDto.from_domain(transfer, progress)


class GetTransferProgressUseCase:
    """Прогресс и ETA без загрузки items — для периодических SSE-событий `progress`."""

    def __init__(self, transfers: TransferRepository) -> None:
        self._transfers = transfers

    async def execute(self, user_id: UUID, transfer_id: UUID) -> TransferProgressDto | None:
        sample = await self._transfers.progress_sample(transfer_id, ETA_WINDOW)
        if sample is None or sample.user_id != user_id:
            return None
        return TransferProgressDto.from_sample(sample, datetime.now(UTC))


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
