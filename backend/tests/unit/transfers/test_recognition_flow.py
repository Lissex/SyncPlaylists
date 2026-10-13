"""Этап 6-2: распознавание по звуку в переносе на фейках — текст не нашёл → трек ждёт
распознавания (перенос не уходит в ревью раньше) → итог → поиск на цели по услышанному;
потолок фазы; «Ещё раз по звуку» на ревью; sweeper (ARCHITECTURE.md 11j)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.modules.catalog.domain.entities import PlatformTrack
from syncplaylists.modules.matching.application.pipeline_factory import (
    DefaultMatchingPipelineFactory,
)
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.recognition.application.eligibility import RecognitionEligibility
from syncplaylists.modules.recognition.application.use_cases import (
    RecognitionLimits,
    RecognizeSourceTrackUseCase,
)
from syncplaylists.modules.recognition.domain.errors import RecognizerBusyError
from syncplaylists.modules.recognition.domain.policy import RecognitionPolicy
from syncplaylists.modules.transfers.application.use_cases import (
    ExpireRecognitionUseCase,
    MatchTransferItemUseCase,
    RecognizeTransferItemUseCase,
    RematchByAudioUseCase,
    RetryItemRecognitionUseCase,
    SweepStaleTransfersUseCase,
)
from syncplaylists.modules.transfers.domain.entities import Transfer, TransferItem
from syncplaylists.modules.transfers.domain.value_objects import (
    NOTE_NO_MATCH,
    NOTE_TIMEOUT,
    NOTE_UNAVAILABLE,
    ExistingPlaylist,
    PlaylistSource,
    RecognitionState,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.shared_kernel.application.ports import TaskLane
from syncplaylists.shared_kernel.domain.search import RecognizedTrack, TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
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
from tests.fakes.recognition import (
    FakeAudioSource,
    FakeAudioSourceRegistry,
    FakeRecognitionLock,
    FakeRecognitionRepository,
    FakeRecognizer,
    FakeTrimmer,
)

_ISRC = ISRC("USUG11800685")
# Перезалив с мусорным названием: по тексту на цели не находится.
_SOURCE = TrackCandidate(
    ref=ExternalTrackRef(Platform.SOUNDCLOUD, "1057094605"),
    title="lucid dreamz (prod. nick mira) [FREE DL]",
    artist="beatstore99",
    duration=Duration(239_836),
)
_TARGET = TrackCandidate(
    ref=ExternalTrackRef(Platform.YANDEX, "33311009"),
    title="Lucid Dreams",
    artist="Juice WRLD",
    duration=Duration(239_836),
    isrc=_ISRC,
)
_HEARD = RecognizedTrack(
    title="Lucid Dreams", artist="Juice WRLD", provider="shazam", isrc=_ISRC, exact=True
)


class Env:
    def __init__(self, *answers: RecognizedTrack | Exception | None) -> None:
        self.uow = FakeUnitOfWork()
        self.transfers = FakeTransferRepository()
        self.task_queue = FakeTaskQueue()
        self.platform_tracks = FakePlatformTrackRepository()
        self.ensure = EnsurePlatformTrackUseCase(
            self.platform_tracks, FakeCanonicalTrackRepository()
        )
        self.matches = FakeTrackMatchRepository()
        self.gateways = FakeGatewayFactory()
        self.target = FakeMusicPlatformGateway(
            Platform.YANDEX, isrc_results={_ISRC.value: [_TARGET]}
        )
        self.gateways.register(self.target)
        normalizer = TrackNormalizer()
        self.pipelines = DefaultMatchingPipelineFactory(
            self.gateways, self.matches, normalizer, MatchScorer(normalizer)
        )
        self.user_id = uuid4()
        self.accounts = FakeAccountAccessProvider()
        self.accounts.connect(self.user_id, Platform.SOUNDCLOUD)
        self.accounts.connect(self.user_id, Platform.YANDEX)
        self.recognizer = FakeRecognizer("shazam", *answers)
        self.recognitions = FakeRecognitionRepository()
        self.eligibility = RecognitionEligibility(
            RecognitionPolicy(frozenset({Platform.SOUNDCLOUD}), min_track_ms=20_000), enabled=True
        )

    def match(self) -> MatchTransferItemUseCase:
        return MatchTransferItemUseCase(
            self.uow,
            self.transfers,
            self.platform_tracks,
            self.matches,
            self.ensure,
            self.pipelines,
            self.task_queue,
            self.accounts,
            self.eligibility,
            timedelta(minutes=15),
        )

    def recognize(self) -> RecognizeTransferItemUseCase:
        source_track = RecognizeSourceTrackUseCase(
            FakeAudioSourceRegistry(FakeAudioSource()),
            FakeTrimmer(),
            [self.recognizer],
            self.recognitions,
            FakeRecognitionLock(),
            self.ensure,
            RecognitionLimits(18_000, timedelta(days=30), timedelta(days=7)),
            clock=lambda: datetime.now(UTC),
        )
        return RecognizeTransferItemUseCase(
            self.uow,
            self.transfers,
            self.platform_tracks,
            self.accounts,
            source_track,
            self.task_queue,
        )

    def rematch(self) -> RematchByAudioUseCase:
        return RematchByAudioUseCase(
            self.uow,
            self.transfers,
            self.platform_tracks,
            self.matches,
            self.ensure,
            self.pipelines,
            self.accounts,
        )

    async def running_transfer(
        self, *tracks: TrackCandidate, recognize_audio: bool = True
    ) -> Transfer:
        transfer = Transfer(
            id=uuid4(),
            user_id=self.user_id,
            source=PlaylistSource(ref=PlaylistRef(Platform.SOUNDCLOUD, "user/sets/x")),
            destination=ExistingPlaylist(ref=PlaylistRef(Platform.YANDEX, "1:3")),
            recognize_audio=recognize_audio,
        )
        transfer.start(datetime.now(UTC))
        for position, track in enumerate(tracks or (_SOURCE,)):
            await self.platform_tracks.get_or_create(
                PlatformTrack(
                    id=uuid4(),
                    platform=track.ref.platform,
                    external_id=track.ref.external_id,
                    raw_title=track.title,
                    raw_artist=track.artist,
                    duration=track.duration,
                    isrc=track.isrc,
                )
            )
            transfer.add_item(position, track.ref)
        await self.transfers.save(transfer)
        return transfer

    async def item(self, transfer: Transfer, position: int = 0) -> TransferItem:
        stored = await self.transfers.get(transfer.id)
        assert stored is not None
        return stored.items[position]

    async def status(self, transfer: Transfer) -> TransferStatus:
        stored = await self.transfers.get(transfer.id)
        assert stored is not None
        return stored.status


async def test_text_miss_waits_for_recognition_instead_of_review() -> None:
    env = Env(_HEARD)
    transfer = await env.running_transfer()

    await env.match().execute(transfer.id, 0)

    item = await env.item(transfer)
    assert item.status is TransferItemStatus.PENDING
    assert item.recognition is not None
    assert item.recognition.state is RecognitionState.QUEUED
    assert item.recognition.text_status is TransferItemStatus.NOT_FOUND
    assert await env.status(transfer) is TransferStatus.RUNNING  # не в ревью раньше времени
    assert env.task_queue.enqueued == [("recognize_track", (transfer.id, 0))]
    assert ("recognize_track", TaskLane.RECOGNIZE) in env.task_queue.lanes
    # Потолок фазы — одна задача на перенос.
    assert [task for task, _, _ in env.task_queue.scheduled] == ["expire_recognition"]


async def test_deadline_scheduled_once_per_transfer() -> None:
    env = Env()
    other = TrackCandidate(
        ref=ExternalTrackRef(Platform.SOUNDCLOUD, "2"),
        title="zzz unknown beat",
        artist="someone",
        duration=Duration(200_000),
    )
    transfer = await env.running_transfer(_SOURCE, other)

    await env.match().execute(transfer.id, 0)
    await env.match().execute(transfer.id, 1)

    assert [task for task, _, _ in env.task_queue.scheduled] == ["expire_recognition"]
    stored = await env.transfers.get(transfer.id)
    assert stored is not None
    assert stored.recognition_deadline_at is not None


async def test_opted_out_transfer_gets_text_result_at_once() -> None:
    env = Env(_HEARD)
    transfer = await env.running_transfer(recognize_audio=False)

    await env.match().execute(transfer.id, 0)

    item = await env.item(transfer)
    assert item.status is TransferItemStatus.NOT_FOUND
    assert item.recognition is None
    assert await env.status(transfer) is TransferStatus.REVIEW


async def test_slowed_source_is_not_recognized() -> None:
    env = Env(_HEARD)
    slowed = TrackCandidate(
        ref=ExternalTrackRef(Platform.SOUNDCLOUD, "3"),
        title="Juice WRLD - Lucid Dreams (Slowed + Reverb)",
        artist="trinxchris",
        duration=Duration(283_971),
    )
    transfer = await env.running_transfer(slowed)

    await env.match().execute(transfer.id, 0)

    assert (await env.item(transfer)).recognition is None
    assert env.recognizer.calls == []


async def test_recognized_then_found_by_audio_and_written() -> None:
    env = Env(_HEARD)
    transfer = await env.running_transfer()
    await env.match().execute(transfer.id, 0)
    env.task_queue.enqueued.clear()

    assert await env.recognize().execute(transfer.id, 0) is None

    item = await env.item(transfer)
    assert item.recognition is not None
    assert item.recognition.state is RecognitionState.READY
    assert item.recognition.heard == _HEARD
    assert env.task_queue.enqueued == [("run_match", (transfer.id, 0))]

    await env.match().execute(transfer.id, 0)

    item = await env.item(transfer)
    assert item.status is TransferItemStatus.MATCHED
    assert item.match is not None
    assert item.match.method == "audio"
    assert item.match.target_ref == _TARGET.ref
    assert item.recognition is not None
    assert item.recognition.note == "exact"
    assert await env.status(transfer) is TransferStatus.WRITING
    # Найденное по звуку — в общем кэше соответствий.
    cached = await env.matches.find(_SOURCE.ref, Platform.YANDEX)
    assert cached is not None
    assert cached.method.value == "audio"


async def test_not_recognized_falls_back_to_text_result() -> None:
    env = Env(None)
    transfer = await env.running_transfer()
    await env.match().execute(transfer.id, 0)

    await env.recognize().execute(transfer.id, 0)
    await env.match().execute(transfer.id, 0)

    item = await env.item(transfer)
    assert item.status is TransferItemStatus.NOT_FOUND
    assert item.recognition is not None
    assert item.recognition.note == NOTE_NO_MATCH
    assert await env.status(transfer) is TransferStatus.REVIEW


async def test_busy_recognizer_defers_task() -> None:
    env = Env(RecognizerBusyError("shazam", 30))
    transfer = await env.running_transfer()
    await env.match().execute(transfer.id, 0)
    env.task_queue.enqueued.clear()

    retry_after = await env.recognize().execute(transfer.id, 0)

    assert retry_after == 30
    item = await env.item(transfer)
    assert item.recognition is not None
    assert item.recognition.state is RecognitionState.QUEUED
    assert env.task_queue.enqueued == []


async def test_busy_past_deadline_gives_up_with_timeout() -> None:
    env = Env(RecognizerBusyError("shazam", 3600))
    transfer = await env.running_transfer()
    await env.match().execute(transfer.id, 0)

    assert await env.recognize().execute(transfer.id, 0) is None

    item = await env.item(transfer)
    assert item.recognition is not None
    assert item.recognition.state is RecognitionState.READY
    assert item.recognition.note == NOTE_TIMEOUT


async def test_last_try_is_unavailable() -> None:
    env = Env(RecognizerBusyError("shazam", 5))
    transfer = await env.running_transfer()
    await env.match().execute(transfer.id, 0)

    await env.recognize().execute(transfer.id, 0, final_try=True)

    item = await env.item(transfer)
    assert item.recognition is not None
    assert item.recognition.note == NOTE_UNAVAILABLE


async def test_phase_deadline_releases_waiting_tracks() -> None:
    env = Env()
    transfer = await env.running_transfer()
    await env.match().execute(transfer.id, 0)
    env.task_queue.enqueued.clear()

    await ExpireRecognitionUseCase(env.uow, env.transfers, env.task_queue).execute(transfer.id)

    assert env.task_queue.enqueued == [("run_match", (transfer.id, 0))]
    await env.match().execute(transfer.id, 0)
    item = await env.item(transfer)
    assert item.status is TransferItemStatus.NOT_FOUND
    assert item.recognition is not None
    assert item.recognition.note == NOTE_TIMEOUT
    assert await env.status(transfer) is TransferStatus.REVIEW


async def test_late_recognition_does_not_override_timeout() -> None:
    env = Env(_HEARD)
    transfer = await env.running_transfer()
    await env.match().execute(transfer.id, 0)
    await ExpireRecognitionUseCase(env.uow, env.transfers, env.task_queue).execute(transfer.id)
    env.task_queue.enqueued.clear()

    await env.recognize().execute(transfer.id, 0)

    item = await env.item(transfer)
    assert item.recognition is not None
    assert item.recognition.note == NOTE_TIMEOUT
    assert env.recognizer.calls == []  # уже не ждал — не распознаём
    assert env.task_queue.enqueued == []


async def test_retry_on_review_finds_by_audio() -> None:
    env = Env(_HEARD)
    transfer = await env.running_transfer()
    await env.match().execute(transfer.id, 0)
    await ExpireRecognitionUseCase(env.uow, env.transfers, env.task_queue).execute(transfer.id)
    await env.match().execute(transfer.id, 0)
    assert await env.status(transfer) is TransferStatus.REVIEW
    env.task_queue.enqueued.clear()

    await RetryItemRecognitionUseCase(env.uow, env.transfers, env.task_queue).execute(
        env.user_id, transfer.id, 0
    )
    assert env.task_queue.enqueued == [("recognize_track", (transfer.id, 0))]
    await env.recognize().execute(transfer.id, 0)
    assert env.task_queue.enqueued[-1] == ("rematch_by_audio", (transfer.id, 0))
    await env.rematch().execute(transfer.id, 0)

    item = await env.item(transfer)
    assert item.status is TransferItemStatus.MATCHED
    assert item.match is not None
    assert item.match.method == "audio"
    assert await env.status(transfer) is TransferStatus.REVIEW  # запись — по кнопке
    sample = await env.transfers.progress_sample(transfer.id, 0)
    assert sample is not None
    assert (sample.progress.matched, sample.progress.not_found) == (1, 0)


async def test_rematch_gives_up_to_text_result() -> None:
    env = Env(_HEARD)
    transfer = await env.running_transfer()
    await env.match().execute(transfer.id, 0)
    await ExpireRecognitionUseCase(env.uow, env.transfers, env.task_queue).execute(transfer.id)
    await env.match().execute(transfer.id, 0)
    await RetryItemRecognitionUseCase(env.uow, env.transfers, env.task_queue).execute(
        env.user_id, transfer.id, 0
    )
    await env.recognize().execute(transfer.id, 0)

    await env.rematch().execute(transfer.id, 0, give_up=True)

    item = await env.item(transfer)
    assert item.status is TransferItemStatus.NOT_FOUND
    assert item.recognition is not None
    assert item.recognition.note == NOTE_UNAVAILABLE


async def test_sweeper_requeues_recognition_not_run_match() -> None:
    env = Env()
    transfer = await env.running_transfer()
    await env.match().execute(transfer.id, 0)
    env.task_queue.enqueued.clear()
    env.transfers.mark_stale(transfer.id, datetime.now(UTC) - timedelta(hours=1))

    await SweepStaleTransfersUseCase(env.transfers, env.task_queue).execute()

    assert env.task_queue.enqueued == [("recognize_track", (transfer.id, 0))]
