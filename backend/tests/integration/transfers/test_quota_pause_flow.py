"""Сквозной сценарий паузы по квоте на настоящем Postgres: 429 посреди матчинга →
перенос на паузе, остальные треки не трогают площадку → resume → все треки
сопоставлены, ни одного FAILED (этап 4b-3). Площадка — тестовый фейк, не Яндекс."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.infrastructure.db.engine import create_engine
from syncplaylists.infrastructure.db.session import create_session_factory
from syncplaylists.infrastructure.db.uow import SqlUnitOfWork
from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.modules.catalog.infrastructure.repository import (
    SqlCanonicalTrackRepository,
    SqlPlatformTrackRepository,
)
from syncplaylists.modules.matching.application.pipeline_factory import (
    DefaultMatchingPipelineFactory,
)
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.infrastructure.repository import SqlTrackMatchRepository
from syncplaylists.modules.transfers.application.use_cases import (
    FailTransferItemUseCase,
    MatchTransferItemUseCase,
    PauseTransferForQuotaUseCase,
    ResumeTransferUseCase,
)
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    PlaylistSource,
    TransferStatus,
)
from syncplaylists.modules.transfers.infrastructure.orm import TransferOrm
from syncplaylists.modules.transfers.infrastructure.repository import SqlTransferRepository
from syncplaylists.modules.transfers.presentation.tasks import match_with_retries
from syncplaylists.shared_kernel.domain.errors import PlatformRateLimitedError
from syncplaylists.shared_kernel.domain.search import TrackCandidate, TrackQuery
from syncplaylists.shared_kernel.domain.value_objects import (
    Duration,
    ExternalTrackRef,
    Platform,
    PlaylistRef,
)
from tests.fakes import FakeEventPublisher, FakeGatewayFactory, FakeMusicPlatformGateway
from tests.fakes.accounts import FakeAccountAccessProvider
from tests.fakes.transfers import FakeTaskQueue

_TRACKS = 4


class _QuotaGateway(FakeMusicPlatformGateway):
    """Находит каждый трек по названию; пока квота «исчерпана» — 429 на 10 минут."""

    def __init__(self) -> None:
        super().__init__(platform=Platform.SPOTIFY)
        self.quota_exhausted = False
        self.real_searches = 0

    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]:
        if self.quota_exhausted:
            raise PlatformRateLimitedError(Platform.SPOTIFY, 600, "HTTP 429")
        self.real_searches += 1
        return [
            TrackCandidate(
                ref=ExternalTrackRef(Platform.SPOTIFY, f"t-{query.title}"),
                title=query.title,
                artist=query.artist or "",
                duration=Duration(200_000),
            )
        ]


class _Env:
    def __init__(self, factory: async_sessionmaker[AsyncSession], user_id: UUID) -> None:
        self.factory = factory
        self.queue = FakeTaskQueue()
        self.gateway = _QuotaGateway()
        self.gateways = FakeGatewayFactory({Platform.SPOTIFY: self.gateway})
        self.accounts = FakeAccountAccessProvider()
        self.accounts.connect(user_id, Platform.VK)
        self.accounts.connect(user_id, Platform.SPOTIFY)

    async def run(self, fn: Any) -> None:
        """Одна «доставка задачи»: своя сессия и транзакция, как в воркере."""
        async with self.factory() as db:
            platform_tracks = SqlPlatformTrackRepository(db)
            transfers = SqlTransferRepository(db, platform_tracks)
            uow = SqlUnitOfWork(db, FakeEventPublisher())
            ensure = EnsurePlatformTrackUseCase(platform_tracks, SqlCanonicalTrackRepository(db))
            matches = SqlTrackMatchRepository(db, platform_tracks)
            normalizer = TrackNormalizer()
            pipelines = DefaultMatchingPipelineFactory(
                self.gateways, matches, normalizer, MatchScorer(normalizer)
            )
            await fn(
                match=MatchTransferItemUseCase(
                    uow,
                    transfers,
                    platform_tracks,
                    matches,
                    ensure,
                    pipelines,
                    self.queue,
                    self.accounts,
                ),
                fail=FailTransferItemUseCase(uow, transfers, self.queue),
                pause=PauseTransferForQuotaUseCase(uow, transfers, self.queue),
                resume=ResumeTransferUseCase(uow, transfers, self.queue),
            )

    async def run_match(self, transfer_id: UUID, position: int) -> None:
        async def go(match: Any, fail: Any, pause: Any, resume: Any) -> None:
            await match_with_retries(1, transfer_id, position, match, fail, pause)

        await self.run(go)

    async def resume(self, transfer_id: UUID) -> None:
        async def go(match: Any, fail: Any, pause: Any, resume: Any) -> None:
            await resume.execute(transfer_id)

        await self.run(go)


async def _seed(factory: async_sessionmaker[AsyncSession], user_id: UUID) -> Transfer:
    run = uuid4().hex[:8]
    transfer = Transfer(
        id=uuid4(),
        user_id=user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, f"src-{run}")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst")),
    )
    transfer.start(datetime.now(UTC))
    async with factory() as db:
        platform_tracks = SqlPlatformTrackRepository(db)
        ensure = EnsurePlatformTrackUseCase(platform_tracks, SqlCanonicalTrackRepository(db))
        for position in range(_TRACKS):
            ref = ExternalTrackRef(Platform.VK, f"{run}-{position}")
            await ensure.execute(ref, f"song {run} {position}", "artist", Duration(200_000), None)
            transfer.add_item(position, ref)
        await SqlTransferRepository(db, platform_tracks).save(transfer)
        await db.commit()
    return transfer


async def _status(factory: async_sessionmaker[AsyncSession], transfer_id: UUID) -> TransferOrm:
    async with factory() as db:
        orm = await db.get(TransferOrm, transfer_id)
        assert orm is not None
        return orm


async def _run_all(env: _Env, transfer_id: UUID, positions: Sequence[int]) -> None:
    for position in positions:
        await env.run_match(transfer_id, position)


async def test_quota_pause_then_resume_finishes_without_failures(
    settings: Settings, user_id: UUID, session: AsyncSession
) -> None:
    await session.commit()  # user_id видят другие сессии
    engine = create_engine(settings.db)
    factory = create_session_factory(engine)
    try:
        env = _Env(factory, user_id)
        transfer = await _seed(factory, user_id)

        await env.run_match(transfer.id, 0)  # первый трек — до исчерпания квоты
        env.gateway.quota_exhausted = True
        await env.run_match(transfer.id, 1)  # 429 → пауза всего переноса
        searches_before = env.gateway.real_searches
        await _run_all(env, transfer.id, [2, 3])  # на паузе — в площадку не ходят

        paused = await _status(factory, transfer.id)
        assert paused.status == TransferStatus.PAUSED_QUOTA.value
        assert paused.paused_from == TransferStatus.RUNNING.value
        assert paused.resume_at is not None
        assert (paused.pending, paused.matched, paused.failed) == (3, 1, 0)
        assert env.gateway.real_searches == searches_before
        assert [task for task, _, _ in env.queue.scheduled] == ["resume_transfer"]

        env.gateway.quota_exhausted = False
        await env.resume(transfer.id)  # до срока — ничего
        assert (await _status(factory, transfer.id)).status == "paused_quota"

        async with factory() as db:  # «прошло 10 минут»
            orm = await db.get(TransferOrm, transfer.id)
            assert orm is not None
            orm.resume_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
        await env.resume(transfer.id)

        requeued = [args[1] for task, args in env.queue.enqueued if task == "run_match"]
        assert requeued == [1, 2, 3]  # только не обработанные
        await _run_all(env, transfer.id, requeued)

        done = await _status(factory, transfer.id)
        assert (done.pending, done.matched, done.failed) == (0, _TRACKS, 0)
        assert done.status == TransferStatus.WRITING.value
        assert (done.resume_at, done.paused_from) == (None, None)
    finally:
        await engine.dispose()
