from datetime import UTC, datetime
from uuid import uuid4

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
    use_case = StartTransferUseCase(env.uow, env.transfers, env.task_queue)
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))

    dto = await use_case.execute(uuid4(), source, destination)

    assert dto.status == "queued"
    assert env.uow.commits == 1
    assert env.transfers.save_calls == 1
    assert env.task_queue.enqueued == [("run_transfer", (dto.id,))]


async def test_process_transfer_reads_playlist_and_enqueues_match_per_item() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=uuid4(), source=source, destination=destination)
    await env.seed_transfer(transfer)
    snapshot = PlaylistSnapshot(
        ref=source.ref, title="My playlist", description=None, tracks=(_VK_TRACK_1,)
    )
    env.register_gateway(FakeMusicPlatformGateway(platform=Platform.VK, playlist=snapshot))

    use_case = ProcessTransferUseCase(
        env.uow,
        env.transfers,
        env.platform_tracks,
        env.ensure_platform_track,
        env.gateway_factory,
        env.task_queue,
    )
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
    transfer = Transfer(id=uuid4(), user_id=uuid4(), source=source, destination=destination)
    await env.seed_transfer(transfer)
    snapshot = PlaylistSnapshot(
        ref=source.ref, title="My playlist", description=None, tracks=(_VK_TRACK_1,)
    )
    env.register_gateway(FakeMusicPlatformGateway(platform=Platform.VK, playlist=snapshot))
    use_case = ProcessTransferUseCase(
        env.uow,
        env.transfers,
        env.platform_tracks,
        env.ensure_platform_track,
        env.gateway_factory,
        env.task_queue,
    )
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
    transfer = Transfer(id=uuid4(), user_id=uuid4(), source=source, destination=destination)
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
    transfer = Transfer(id=uuid4(), user_id=uuid4(), source=source, destination=destination)
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
    transfer = Transfer(id=uuid4(), user_id=uuid4(), source=source, destination=destination)
    now = datetime.now(UTC)
    transfer.start(now)
    transfer.add_item(0, _VK_TRACK_1.ref)
    transfer.record_uncertain(0, (_SPOTIFY_MATCH_1,), now)
    transfer.enter_review()
    await env.seed_transfer(transfer)

    use_case = ResolveUncertainItemUseCase(env.uow, env.transfers, env.task_queue)
    await use_case.execute(transfer.id, 0, _SPOTIFY_MATCH_1.ref)

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.WRITING
    assert env.task_queue.enqueued == [("run_write", (transfer.id,))]


async def test_write_transfer_adds_matched_items_and_completes() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist"))
    transfer = Transfer(id=uuid4(), user_id=uuid4(), source=source, destination=destination)
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

    use_case = WriteTransferUseCase(env.uow, env.transfers, env.gateway_factory)
    await use_case.execute(transfer.id)

    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.status is TransferStatus.DONE
    assert stored.items[0].status is TransferItemStatus.ADDED
    assert target_gateway.added_to_playlist == [_SPOTIFY_MATCH_1.ref]


async def test_write_transfer_to_library_reverses_order_for_top_insert() -> None:
    env = Env()
    source = PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist"))
    destination = LibraryDestination(platform=Platform.SPOTIFY, account_id=uuid4())
    transfer = Transfer(id=uuid4(), user_id=uuid4(), source=source, destination=destination)
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

    use_case = WriteTransferUseCase(env.uow, env.transfers, env.gateway_factory)
    await use_case.execute(transfer.id)

    assert target_gateway.added_to_library == [ref_b, ref_a]


async def test_get_transfer_returns_dto() -> None:
    env = Env()
    source = LibrarySource(platform=Platform.VK, account_id=uuid4())
    destination = LibraryDestination(platform=Platform.SPOTIFY, account_id=uuid4())
    transfer = Transfer(id=uuid4(), user_id=uuid4(), source=source, destination=destination)
    await env.seed_transfer(transfer)

    use_case = GetTransferUseCase(env.transfers)
    dto = await use_case.execute(transfer.id)

    assert dto is not None
    assert dto.id == transfer.id
    assert dto.status == "queued"


async def test_get_transfer_returns_none_for_unknown_id() -> None:
    env = Env()
    use_case = GetTransferUseCase(env.transfers)

    assert await use_case.execute(uuid4()) is None
