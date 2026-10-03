"""PAUSED_QUOTA в агрегате: пауза всего переноса до resume_at вместо повторов по
каждому треку (этап 4b-3)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.errors import InvalidTransferTransitionError
from syncplaylists.modules.transfers.domain.events import TransferPausedForQuota, TransferResumed
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    PlaylistSource,
    TransferStatus,
)
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    Platform,
    PlaylistRef,
)

_NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _running() -> Transfer:
    transfer = Transfer(
        id=uuid4(),
        user_id=uuid4(),
        source=PlaylistSource(ref=PlaylistRef(Platform.SOUNDCLOUD, "u/sets/s")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.YANDEX, "u:1")),
    )
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.SOUNDCLOUD, "1"))
    transfer.pull_domain_events()
    return transfer


def test_pause_and_resume_returns_to_phase() -> None:
    transfer = _running()
    resume_at = _NOW + timedelta(minutes=10)

    assert transfer.pause_for_quota(resume_at, _NOW)
    assert transfer.status is TransferStatus.PAUSED_QUOTA
    assert (transfer.resume_at, transfer.paused_from) == (resume_at, TransferStatus.RUNNING)

    assert transfer.resume_after_quota(_NOW) is TransferStatus.RUNNING
    assert (transfer.status, transfer.resume_at, transfer.paused_from) == (
        TransferStatus.RUNNING,
        None,
        None,
    )
    events = transfer.pull_domain_events()
    assert [type(e) for e in events] == [TransferPausedForQuota, TransferResumed]


def test_second_429_only_extends_pause() -> None:
    transfer = _running()
    later = _NOW + timedelta(minutes=10)
    transfer.pause_for_quota(later, _NOW)

    assert not transfer.pause_for_quota(_NOW + timedelta(minutes=5), _NOW)  # раньше — нет
    assert transfer.resume_at == later
    assert transfer.pause_for_quota(later + timedelta(minutes=1), _NOW)
    assert transfer.resume_at == later + timedelta(minutes=1)
    assert transfer.paused_from is TransferStatus.RUNNING  # фаза не потерялась


def test_item_started_before_pause_still_records_result() -> None:
    transfer = _running()
    item = transfer.items[0]
    transfer.pause_for_quota(_NOW + timedelta(minutes=10), _NOW)
    item.apply_not_found(())
    transfer.record_item_outcome(item, _NOW)  # не бросает


@pytest.mark.parametrize("status", [TransferStatus.REVIEW, TransferStatus.DONE])
def test_cannot_pause_outside_platform_phases(status: TransferStatus) -> None:
    transfer = _running()
    transfer.status = status
    with pytest.raises(InvalidTransferTransitionError):
        transfer.pause_for_quota(_NOW, _NOW)


def test_cannot_resume_when_not_paused() -> None:
    with pytest.raises(InvalidTransferTransitionError):
        _running().resume_after_quota(_NOW)
