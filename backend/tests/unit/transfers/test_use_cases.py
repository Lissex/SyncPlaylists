from datetime import UTC, datetime
from uuid import uuid4

import pytest

from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.modules.catalog.domain.entities import PlatformTrack
from syncplaylists.modules.matching.application.pipeline_factory import (
    DefaultMatchingPipelineFactory,
)
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.transfers.application.use_cases import (
    GetTransferUseCase,
    MatchTransferItemUseCase,
    ProcessTransferUseCase,
    ResolveUncertainItemUseCase,
    StartTransferUseCase,
    TransferNotFoundError,
    WriteTransferUseCase,
)
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    LibraryDestination,
    LibrarySource,
    MatchResult,
    PlaylistSource,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.shared_kernel.application.ports import AccountNotAvailableError
from syncplaylists.shared_kernel.domain.search import InsertOrder, PlaylistSnapshot, TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import (
    Duration,
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)
from tests.fakes import (
    FakeCanonicalTrackRepository,
    FakeGatewayFactory,
    FakeMusicPlatformGateway,
    FakePlatformTrackRepository,
    FakeTaskQueue,
    FakeTrackMatchRepository,
    FakeTransferRepository,
    FakeUnitOfWork,
)
from tests.fakes.accounts import FakeAccountAccessProvider

_VK_TRACK_1 = TrackCandidate(
    ref=ExternalTrackRef(Platform.VK, "src-1"),
    title="Starboy",
    artist="The Weeknd",
    duration=Duration(230_000),
)
_SPOTIFY_MATCH_1 = TrackCandidate(
    ref=ExternalTrackRef(Platform.SPOTIFY, "tgt-1"),
    title="Starboy",
    artist="The Weeknd",
    duration=Duration(230_000),
)


class Env:
    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.transfers = FakeTransferRepository()
        self.task_queue = FakeTaskQueue()
        self.platform_tracks = FakePlatformTrackRepository()
        self.canonical_tracks = FakeCanonicalTrackRepository()
        self.ensure_platform_track = EnsurePlatformTrackUseCase(
            self.platform_tracks, self.canonical_tracks
        )
        self.gateway_factory = FakeGatewayFactory()
        self.match_repository = FakeTrackMatchRepository()
        self.normalizer = TrackNormalizer()
        self.scorer = MatchScorer(self.normalizer)
        self.user_id = uuid4()
        self.accounts = FakeAccountAccessProvider()
        self.vk = self.accounts.connect(self.user_id, Platform.VK)
        self.spotify = self.accounts.connect(self.user_id, Platform.SPOTIFY)

    def match_transfer_item_use_case(self) -> MatchTransferItemUseCase:
        pipeline_factory = DefaultMatchingPipelineFactory(
            self.gateway_factory, self.match_repository, self.normalizer, self.scorer
        )
        return MatchTransferItemUseCase(
            self.uow,
            self.transfers,
            self.platform_tracks,
            self.match_repository,
            self.ensure_platform_track,
            pipeline_factory,
            self.task_queue,
            self.accounts,
        )

    def process_transfer_use_case(self) -> ProcessTransferUseCase:
        return ProcessTransferUseCase(
            self.uow,
            self.transfers,
            self.platform_tracks,
            self.ensure_platform_track,
            self.gateway_factory,
            self.task_queue,
            self.accounts,
        )

    def register_target_search_results(
        self, platform: Platform, results: list[TrackCandidate]
    ) -> None:
        self.gateway_factory.register(
            FakeMusicPlatformGateway(platform=platform, search_results=results)
        )

    def register_gateway(self, gateway: FakeMusicPlatformGateway) -> None:
        self.gateway_factory.register(gateway)

    async def seed_transfer(self, transfer: Transfer) -> None:
        await self.transfers.save(transfer)

    async def seed_source_platform_track(self, candidate: TrackCandidate) -> None:
        await self.platform_tracks.get_or_create(
            PlatformTrack(
                id=uuid4(),
                platform=candidate.ref.platform,
                external_id=candidate.ref.external_id,
                raw_title=candidate.title,
                raw_artist=candidate.artist,
                duration=candidate.duration,
                isrc=candidate.isrc,
            )
        )


async def test_start_transfer_creates_queued_transfer_and_enqueues_run_transfer() -> None:
    env = Env()
    use_case = StartTransferUseCase(env.uow, env.transfers, env.task_queue, env.accounts)
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))

    dto = await use_case.execute(env.user_id, source, destination)

    assert dto.status == "queued"
    assert env.uow.commits == 1
    assert env.transfers.save_calls == 1
    assert env.task_queue.enqueued == [("run_transfer", (dto.id,))]


async def test_process_transfer_reads_playlist_and_enqueues_match_per_item() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    await env.seed_transfer(transfer)
    snapshot = PlaylistSnapshot(
        ref=source.ref, title="My playlist", description=None, tracks=(_VK_TRACK_1,)
    )
    env.register_gateway(FakeMusicPlatformGateway(platform=Platform.VK, playlist=snapshot))

    use_case = env.process_transfer_use_case()
    await use_case.execute(transfer.id)

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.RUNNING
    assert len(stored.items) == 1
    assert stored.items[0].source_track == _VK_TRACK_1.ref
    assert env.task_queue.enqueued == [("run_match", (transfer.id, 0))]


async def test_process_transfer_is_idempotent_on_redelivery() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    await env.seed_transfer(transfer)
    snapshot = PlaylistSnapshot(
        ref=source.ref, title="My playlist", description=None, tracks=(_VK_TRACK_1,)
    )
    env.register_gateway(FakeMusicPlatformGateway(platform=Platform.VK, playlist=snapshot))
    use_case = env.process_transfer_use_case()
    await use_case.execute(transfer.id)

    await use_case.execute(transfer.id)  # повторная доставка ARQ

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert len(stored.items) == 1  # не задвоилось
    assert env.task_queue.enqueued == [("run_match", (transfer.id, 0))]


async def test_match_transfer_item_records_match_and_triggers_write_for_last_item() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    transfer.start(datetime.now(UTC))
    transfer.add_item(0, _VK_TRACK_1.ref)
    await env.seed_source_platform_track(_VK_TRACK_1)
    await env.seed_transfer(transfer)
    env.register_target_search_results(Platform.SPOTIFY, [_SPOTIFY_MATCH_1])

    use_case = env.match_transfer_item_use_case()
    await use_case.execute(transfer.id, 0)

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.WRITING
    assert stored.items[0].status is TransferItemStatus.MATCHED
    assert stored.items[0].match is not None
    assert stored.items[0].match.target_ref == _SPOTIFY_MATCH_1.ref
    assert env.task_queue.enqueued == [("run_write", (transfer.id,))]


async def test_match_transfer_item_is_idempotent_on_redelivery() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    transfer.start(datetime.now(UTC))
    transfer.add_item(0, _VK_TRACK_1.ref)
    await env.seed_source_platform_track(_VK_TRACK_1)
    await env.seed_transfer(transfer)
    env.register_target_search_results(Platform.SPOTIFY, [_SPOTIFY_MATCH_1])
    use_case = env.match_transfer_item_use_case()
    await use_case.execute(transfer.id, 0)

    await use_case.execute(transfer.id, 0)  # повторная доставка ARQ

    assert env.task_queue.enqueued == [("run_write", (transfer.id,))]  # не задвоилось


async def test_resolve_uncertain_item_triggers_write_when_last_unresolved() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    now = datetime.now(UTC)
    transfer.start(now)
    transfer.add_item(0, _VK_TRACK_1.ref)
    transfer.record_uncertain(0, (_SPOTIFY_MATCH_1,), now)
    transfer.enter_review()
    await env.seed_transfer(transfer)

    use_case = ResolveUncertainItemUseCase(env.uow, env.transfers, env.task_queue)
    await use_case.execute(env.user_id, transfer.id, 0, _SPOTIFY_MATCH_1.ref)

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.WRITING
    assert env.task_queue.enqueued == [("run_write", (transfer.id,))]


async def test_write_transfer_adds_matched_items_and_completes() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    now = datetime.now(UTC)
    transfer.start(now)
    transfer.add_item(0, _VK_TRACK_1.ref)
    transfer.record_match(
        0, MatchResult(target_ref=_SPOTIFY_MATCH_1.ref, method="fuzzy", score=MatchScore(0.95)), now
    )
    transfer.begin_writing(now)
    await env.seed_transfer(transfer)
    target_gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY)
    env.register_gateway(target_gateway)

    use_case = WriteTransferUseCase(env.uow, env.transfers, env.gateway_factory, env.accounts)
    await use_case.execute(transfer.id)

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.DONE
    assert stored.items[0].status is TransferItemStatus.ADDED
    assert target_gateway.added_to_playlist == [_SPOTIFY_MATCH_1.ref]


async def test_write_transfer_to_library_reverses_order_for_top_insert() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = LibraryDestination(platform=Platform.SPOTIFY, account_id=env.spotify.account_id)
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    now = datetime.now(UTC)
    transfer.start(now)
    ref_a = ExternalTrackRef(Platform.SPOTIFY, "tgt-a")
    ref_b = ExternalTrackRef(Platform.SPOTIFY, "tgt-b")
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-a"))
    transfer.add_item(1, ExternalTrackRef(Platform.VK, "src-b"))
    transfer.record_match(
        0, MatchResult(target_ref=ref_a, method="fuzzy", score=MatchScore(0.95)), now
    )
    transfer.record_match(
        1, MatchResult(target_ref=ref_b, method="fuzzy", score=MatchScore(0.95)), now
    )
    transfer.begin_writing(now)
    await env.seed_transfer(transfer)
    target_gateway = FakeMusicPlatformGateway(
        platform=Platform.SPOTIFY, insert_order=InsertOrder.TOP
    )
    env.register_gateway(target_gateway)

    use_case = WriteTransferUseCase(env.uow, env.transfers, env.gateway_factory, env.accounts)
    await use_case.execute(transfer.id)

    assert target_gateway.added_to_library == [ref_b, ref_a]


async def test_get_transfer_returns_dto() -> None:
    env = Env()
    source = LibrarySource(platform=Platform.VK, account_id=env.vk.account_id)
    destination = LibraryDestination(platform=Platform.SPOTIFY, account_id=env.spotify.account_id)
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    await env.seed_transfer(transfer)

    use_case = GetTransferUseCase(env.transfers)
    dto = await use_case.execute(env.user_id, transfer.id)

    assert dto is not None
    assert dto.id == transfer.id
    assert dto.status == "queued"


async def test_get_transfer_returns_none_for_unknown_id() -> None:
    env = Env()
    use_case = GetTransferUseCase(env.transfers)

    assert await use_case.execute(env.user_id, uuid4()) is None


# --- аккаунты (этап 4a) ---


async def test_start_transfer_rejects_foreign_library_account() -> None:
    env = Env()
    use_case = StartTransferUseCase(env.uow, env.transfers, env.task_queue, env.accounts)
    stranger = env.accounts.connect(uuid4(), Platform.SPOTIFY)
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = LibraryDestination(platform=Platform.SPOTIFY, account_id=stranger.account_id)

    with pytest.raises(AccountNotAvailableError):
        await use_case.execute(env.user_id, source, destination)
    assert env.transfers.save_calls == 0
    assert env.task_queue.enqueued == []


async def test_start_transfer_rejects_account_of_other_platform() -> None:
    env = Env()
    use_case = StartTransferUseCase(env.uow, env.transfers, env.task_queue, env.accounts)
    source = LibrarySource(platform=Platform.YANDEX, account_id=env.vk.account_id)
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))

    with pytest.raises(AccountNotAvailableError):
        await use_case.execute(env.user_id, source, destination)


async def test_start_transfer_requires_account_on_destination_platform() -> None:
    env = Env()
    use_case = StartTransferUseCase(env.uow, env.transfers, env.task_queue, env.accounts)
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.YANDEX, "dst-playlist"))

    with pytest.raises(AccountNotAvailableError):
        await use_case.execute(env.user_id, source, destination)


async def test_process_transfer_uses_users_source_account() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    await env.seed_transfer(transfer)
    snapshot = PlaylistSnapshot(ref=source.ref, title="t", description=None, tracks=())
    env.register_gateway(FakeMusicPlatformGateway(platform=Platform.VK, playlist=snapshot))

    await env.process_transfer_use_case().execute(transfer.id)

    assert env.gateway_factory.accesses == [env.vk]


async def test_process_transfer_fails_when_account_disconnected() -> None:
    env = Env()
    source = LibrarySource(platform=Platform.VK, account_id=env.vk.account_id)
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    await env.seed_transfer(transfer)
    env.accounts.disconnect(env.vk.account_id)

    await env.process_transfer_use_case().execute(transfer.id)

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.FAILED
    assert env.task_queue.enqueued == []


async def test_match_transfer_item_fails_when_destination_account_disconnected() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    transfer.start(datetime.now(UTC))
    transfer.add_item(0, _VK_TRACK_1.ref)
    transfer.add_item(1, ExternalTrackRef(Platform.VK, "src-2"))
    await env.seed_source_platform_track(_VK_TRACK_1)
    await env.seed_transfer(transfer)
    env.accounts.disconnect(env.spotify.account_id)
    use_case = env.match_transfer_item_use_case()

    await use_case.execute(transfer.id, 0)
    await use_case.execute(transfer.id, 1)  # уже FAILED — no-op, не падает

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.FAILED
    assert all(item.status is TransferItemStatus.PENDING for item in stored.items)


async def test_write_transfer_fails_when_destination_account_disconnected() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = LibraryDestination(platform=Platform.SPOTIFY, account_id=env.spotify.account_id)
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    now = datetime.now(UTC)
    transfer.start(now)
    transfer.add_item(0, _VK_TRACK_1.ref)
    transfer.record_match(
        0, MatchResult(target_ref=_SPOTIFY_MATCH_1.ref, method="fuzzy", score=MatchScore(0.95)), now
    )
    transfer.begin_writing(now)
    await env.seed_transfer(transfer)
    target_gateway = FakeMusicPlatformGateway(platform=Platform.SPOTIFY)
    env.register_gateway(target_gateway)
    env.accounts.disconnect(env.spotify.account_id)

    await WriteTransferUseCase(env.uow, env.transfers, env.gateway_factory, env.accounts).execute(
        transfer.id
    )

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.FAILED
    assert target_gateway.added_to_library == []


async def test_get_transfer_hides_foreign_transfer() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    await env.seed_transfer(transfer)

    assert await GetTransferUseCase(env.transfers).execute(uuid4(), transfer.id) is None


async def test_resolve_item_of_foreign_transfer_is_not_found() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=env.user_id, source=source, destination=destination)
    await env.seed_transfer(transfer)
    use_case = ResolveUncertainItemUseCase(env.uow, env.transfers, env.task_queue)

    with pytest.raises(TransferNotFoundError):
        await use_case.execute(uuid4(), transfer.id, 0, None)
