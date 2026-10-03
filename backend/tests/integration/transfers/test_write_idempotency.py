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
from syncplaylists.modules.catalog.infrastructure.repository import SqlPlatformTrackRepository
from syncplaylists.modules.transfers.application.use_cases import WriteTransferUseCase
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import (
    NewPlaylist,
    PlaylistSource,
    TransferStatus,
)
from syncplaylists.modules.transfers.infrastructure.repository import SqlTransferRepository
from syncplaylists.shared_kernel.domain.errors import PlatformUnavailableError
from syncplaylists.shared_kernel.domain.search import AddResult
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    Platform,
    PlaylistRef,
)
from tests.fakes import FakeEventPublisher, FakeGatewayFactory, FakeMusicPlatformGateway
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
                SqlUnitOfWork(db, FakeEventPublisher()), repo, gateways, accounts
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
