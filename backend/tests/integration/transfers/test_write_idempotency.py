"""run_write не создаёт второй плейлист при повторе: create_playlist — отдельный шаг с
немедленным коммитом resolved_target. Проверяется на настоящем Postgres: in-memory
фейк репозитория не откатывает изменения, и долг на нём не воспроизвести."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.infrastructure.db.engine import create_engine
from syncplaylists.infrastructure.db.session import create_session_factory
from syncplaylists.infrastructure.db.uow import SqlUnitOfWork
from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.modules.catalog.infrastructure.repository import (
    SqlCanonicalTrackRepository,
    SqlPlatformTrackRepository,
)
from syncplaylists.modules.transfers.application.use_cases import WriteTransferUseCase
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import (
    MatchResult,
    NewPlaylist,
    PlaylistSource,
    TransferStatus,
)
from syncplaylists.modules.transfers.infrastructure.repository import SqlTransferRepository
from syncplaylists.shared_kernel.domain.errors import PlatformUnavailableError
from syncplaylists.shared_kernel.domain.search import AddResult
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)
from tests.fakes import (
    FakeEventPublisher,
    FakeGatewayFactory,
    FakeMusicPlatformGateway,
    FakeTaskQueue,
)
from tests.fakes.accounts import FakeAccountAccessProvider


class _FlakyWriteGateway(FakeMusicPlatformGateway):
    """create_playlist удаётся, первая запись треков падает временной ошибкой."""

    def __init__(self) -> None:
        super().__init__(platform=Platform.YANDEX)
        self.add_attempts = 0

    async def add_tracks(
        self, playlist: PlaylistRef, tracks: Sequence[ExternalTrackRef]
    ) -> AddResult:
        self.add_attempts += 1
        if self.add_attempts == 1:
            raise PlatformUnavailableError(Platform.YANDEX, "сбой сети после create")
        return await super().add_tracks(playlist, tracks)


async def test_retry_after_failure_reuses_created_playlist(
    settings: Settings, user_id: UUID, session: AsyncSession
) -> None:
    # user_id создан в фикстурной сессии — фиксируем его, чтобы видели другие сессии.
    await session.commit()
    engine = create_engine(settings.db)
    factory = create_session_factory(engine)
    gateway = _FlakyWriteGateway()
    gateways = FakeGatewayFactory({Platform.YANDEX: gateway})
    accounts = FakeAccountAccessProvider()
    accounts.connect(user_id, Platform.YANDEX)

    transfer = Transfer(
        id=uuid4(),
        user_id=user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.YANDEX, "someone:1")),
        destination=NewPlaylist(platform=Platform.YANDEX, title="Копия", description=None),
    )
    now = datetime.now(UTC)
    transfer.start(now)
    transfer.begin_writing(now)  # без items: только создание плейлиста и запись
    async with factory() as setup:
        await SqlTransferRepository(setup, SqlPlatformTrackRepository(setup)).save(transfer)
        await setup.commit()

    async def run_write() -> None:
        # Каждая доставка run_write — своя сессия и транзакция, как в воркере.
        async with factory() as db:
            repo = SqlTransferRepository(db, SqlPlatformTrackRepository(db))
            use_case = WriteTransferUseCase(
                SqlUnitOfWork(db, FakeEventPublisher()), repo, gateways, accounts, FakeTaskQueue()
            )
            await use_case.execute(transfer.id)

    try:
        with pytest.raises(PlatformUnavailableError):
            await run_write()  # create прошёл, запись упала → транзакция откатилась
        await run_write()  # повтор ARQ

        async with factory() as check:
            stored = await SqlTransferRepository(check, SqlPlatformTrackRepository(check)).get(
                transfer.id
            )
        assert stored is not None
        assert stored.status is TransferStatus.DONE
        assert len(gateway.created_playlists) == 1  # плейлист создан ровно один раз
        assert stored.resolved_target == PlaylistRef(Platform.YANDEX, "created-1")
        assert gateway.add_attempts == 2
    finally:
        await engine.dispose()


class _FlakyCreateGateway(FakeMusicPlatformGateway):
    """Плейлист вмещает 1 трек; второе создание (часть 2/2) падает временной ошибкой."""

    def __init__(self) -> None:
        super().__init__(platform=Platform.SOUNDCLOUD, playlist_capacity=1)
        self.create_attempts = 0

    async def create_playlist(
        self, title: str, description: str | None, *, request_id: str | None = None
    ) -> PlaylistRef:
        self.create_attempts += 1
        if self.create_attempts == 2:
            raise PlatformUnavailableError(Platform.SOUNDCLOUD, "сбой между частями")
        return await super().create_playlist(title, description)


async def test_retry_between_parts_creates_each_part_once(
    settings: Settings, user_id: UUID, session: AsyncSession
) -> None:
    await session.commit()
    engine = create_engine(settings.db)
    factory = create_session_factory(engine)
    gateway = _FlakyCreateGateway()
    gateways = FakeGatewayFactory({Platform.SOUNDCLOUD: gateway})
    accounts = FakeAccountAccessProvider()
    accounts.connect(user_id, Platform.SOUNDCLOUD)

    source_refs = [ExternalTrackRef(Platform.YANDEX, f"{n}:1") for n in (101, 102)]
    target_refs = [ExternalTrackRef(Platform.SOUNDCLOUD, str(n)) for n in (201, 202)]
    transfer = Transfer(
        id=uuid4(),
        user_id=user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.YANDEX, "someone:2")),
        destination=NewPlaylist(platform=Platform.SOUNDCLOUD, title="Большой", description=None),
    )
    now = datetime.now(UTC)
    transfer.start(now)
    for position, (source_ref, target_ref) in enumerate(zip(source_refs, target_refs, strict=True)):
        transfer.add_item(position, source_ref)
        result = MatchResult(target_ref=target_ref, method="fuzzy", score=MatchScore(0.95))
        transfer.record_match(position, result, now)
    transfer.begin_writing(now)
    async with factory() as setup:
        platform_tracks = SqlPlatformTrackRepository(setup)
        ensure = EnsurePlatformTrackUseCase(platform_tracks, SqlCanonicalTrackRepository(setup))
        for ref in source_refs:
            await ensure.execute(ref, f"track {ref.external_id}", "artist", None, None)
        await SqlTransferRepository(setup, platform_tracks).save(transfer)
        await setup.commit()

    async def run_write() -> None:
        async with factory() as db:
            repo = SqlTransferRepository(db, SqlPlatformTrackRepository(db))
            use_case = WriteTransferUseCase(
                SqlUnitOfWork(db, FakeEventPublisher()), repo, gateways, accounts, FakeTaskQueue()
            )
            await use_case.execute(transfer.id)

    try:
        with pytest.raises(PlatformUnavailableError):
            await run_write()  # часть 1/2 создана и закоммичена, создание 2/2 упало
        await run_write()

        async with factory() as check:
            stored = await SqlTransferRepository(check, SqlPlatformTrackRepository(check)).get(
                transfer.id
            )
        assert stored is not None
        assert stored.status is TransferStatus.DONE
        assert gateway.created_playlists == [("Большой (1/2)", None), ("Большой (2/2)", None)]
        assert stored.resolved_targets == (
            PlaylistRef(Platform.SOUNDCLOUD, "created-1"),
            PlaylistRef(Platform.SOUNDCLOUD, "created-2"),
        )
        assert gateway.playlist_contents == {
            stored.resolved_targets[0]: [target_refs[0]],
            stored.resolved_targets[1]: [target_refs[1]],
        }
    finally:
        await engine.dispose()
