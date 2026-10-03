from datetime import UTC, datetime, timedelta
from uuid import uuid4

from syncplaylists.modules.transfers.application.use_cases import (
    GetTransferProgressUseCase,
    GetTransferUseCase,
)
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.progress import ETA_WINDOW, estimate_remaining
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    PlaylistSource,
)
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    Platform,
    PlaylistRef,
)
from tests.fakes import FakeTransferRepository

_NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)


def _every(seconds: float, count: int, end: datetime = _NOW) -> list[datetime]:
    return [end - timedelta(seconds=seconds * i) for i in range(count)]


def test_eta_from_recent_speed() -> None:
    # 10 треков, по одному в секунду, последний — только что: ~1 с на трек.
    processed = _every(1.0, 10)

    eta = estimate_remaining(processed, pending=30, now=_NOW)

    assert eta is not None
    assert 29 <= eta.total_seconds() <= 30


def test_eta_grows_during_pause() -> None:
    # Те же 10 треков, но последний обработан 10 минут назад — площадка на паузе.
    processed = _every(1.0, 10, end=_NOW - timedelta(minutes=10))

    eta = estimate_remaining(processed, pending=30, now=_NOW)

    assert eta is not None
    assert eta.total_seconds() > 30 * 60  # оценка выросла, а не застыла на 30 с


def test_eta_uses_only_recent_window() -> None:
    # Давние медленные треки не должны тянуть оценку: важна текущая скорость.
    slow_old = _every(60.0, 50, end=_NOW - timedelta(hours=1))
    fast_recent = _every(0.5, ETA_WINDOW)

    eta = estimate_remaining(slow_old + fast_recent, pending=10, now=_NOW)

    assert eta is not None
    assert eta.total_seconds() < 10


def test_eta_unknown_without_enough_samples() -> None:
    assert estimate_remaining([], pending=5, now=_NOW) is None
    assert estimate_remaining([_NOW], pending=5, now=_NOW) is None


def test_eta_zero_when_nothing_pending() -> None:
    assert estimate_remaining([], pending=0, now=_NOW) == timedelta(0)


# --- use cases: прогресс и ETA в GET /transfers/{id} и SSE --------------------------


async def _running_transfer(repo: FakeTransferRepository, processed: list[datetime]) -> Transfer:
    transfer = Transfer(
        id=uuid4(),
        user_id=uuid4(),
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.YANDEX, "dst")),
    )
    transfer.start(datetime.now(UTC))
    for position in range(100):
        transfer.add_item(position, ExternalTrackRef(Platform.VK, f"t{position}"))
    await repo.save(transfer)
    repo.processed_at[transfer.id] = processed
    return transfer


async def test_get_transfer_includes_progress_and_eta_while_running() -> None:
    repo = FakeTransferRepository()
    now = datetime.now(UTC)
    transfer = await _running_transfer(repo, _every(0.5, 10, end=now))

    dto = await GetTransferUseCase(repo).execute(transfer.user_id, transfer.id)

    assert dto is not None
    assert dto.progress is not None
    assert dto.progress.status == "running"
    assert dto.progress.total == 100
    assert dto.progress.pending == 100  # в фейке items не меняли — важна только ETA
    assert dto.progress.eta_seconds is not None
    assert 45 <= dto.progress.eta_seconds <= 70  # ~0,5 с на трек × 100


async def test_eta_is_null_until_enough_tracks_processed() -> None:
    repo = FakeTransferRepository()
    transfer = await _running_transfer(repo, [])

    dto = await GetTransferProgressUseCase(repo).execute(transfer.user_id, transfer.id)

    assert dto is not None
    assert dto.eta_seconds is None


async def test_progress_of_foreign_transfer_is_hidden() -> None:
    repo = FakeTransferRepository()
    transfer = await _running_transfer(repo, [])

    assert await GetTransferProgressUseCase(repo).execute(uuid4(), transfer.id) is None


async def test_paused_by_quota_shows_resume_time_instead_of_eta() -> None:
    repo = FakeTransferRepository()
    now = datetime.now(UTC)
    transfer = await _running_transfer(repo, _every(0.5, 10, end=now))
    resume_at = now + timedelta(minutes=10)
    await repo.pause_for_quota(transfer.id, resume_at)

    dto = await GetTransferProgressUseCase(repo).execute(transfer.user_id, transfer.id)

    assert dto is not None
    assert dto.status == "paused_quota"
    assert dto.resume_at == resume_at  # фронт: «продолжим в HH:MM»
    assert dto.eta_seconds is None
