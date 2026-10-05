"""PAUSED_CLIENT в агрегате: операцию на площадке выполняет браузерное расширение, а
оно сейчас не может — перенос ждёт без срока и продолжает с той же фазы (этап 4c)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.errors import InvalidTransferTransitionError
from syncplaylists.modules.transfers.domain.events import (
    TrackMatched,
    TransferPausedForClient,
    TransferResumed,
)
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    MatchResult,
    PlaylistSource,
    TransferStatus,
)
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)

_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _running() -> Transfer:
    transfer = Transfer(
        id=uuid4(),
        user_id=uuid4(),
        source=PlaylistSource(ref=PlaylistRef(Platform.YANDEX, "u:1")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SOUNDCLOUD, "123")),
    )
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.YANDEX, "1"))
    transfer.pull_domain_events()
    return transfer


def test_pause_and_resume_returns_to_phase() -> None:
    transfer = _running()

    assert transfer.pause_for_client("offline", _NOW)
    assert transfer.status is TransferStatus.PAUSED_CLIENT
    assert transfer.paused_from is TransferStatus.RUNNING
    assert transfer.pause_reason == "offline"
    assert transfer.resume_at is None  # без срока, в отличие от квоты

    assert transfer.resume_after_client(_NOW) is TransferStatus.RUNNING
    assert transfer.status is TransferStatus.RUNNING
    assert transfer.paused_from is None
    assert transfer.pause_reason is None
    events = transfer.pull_domain_events()
    assert [type(e) for e in events] == [TransferPausedForClient, TransferResumed]
    assert isinstance(events[0], TransferPausedForClient)
    assert events[0].reason == "offline"


def test_repeated_pause_with_same_reason_gives_no_event() -> None:
    transfer = _running()
    transfer.pause_for_client("offline", _NOW)
    transfer.pull_domain_events()

    assert not transfer.pause_for_client("offline", _NOW)
    assert transfer.pull_domain_events() == []


def test_new_reason_updates_pause_and_keeps_phase() -> None:
    transfer = _running()
    transfer.pause_for_client("offline", _NOW)
    transfer.pull_domain_events()

    assert transfer.pause_for_client("captcha", _NOW)
    assert transfer.pause_reason == "captcha"
    assert transfer.paused_from is TransferStatus.RUNNING
    assert [type(e) for e in transfer.pull_domain_events()] == [TransferPausedForClient]


def test_writing_pauses_and_resumes_to_writing() -> None:
    transfer = _running()
    transfer.record_match(
        0,
        MatchResult(
            target_ref=ExternalTrackRef(Platform.SOUNDCLOUD, "9"),
            method="isrc",
            score=MatchScore(1.0),
        ),
        _NOW,
    )
    transfer.begin_writing(_NOW)

    transfer.pause_for_client("timeout", _NOW)
    assert transfer.resume_after_client(_NOW) is TransferStatus.WRITING


def test_item_outcome_is_accepted_while_paused() -> None:
    """Трек, начатый до паузы, дописывает свой результат."""
    transfer = _running()
    transfer.pause_for_client("offline", _NOW)
    item = transfer.items[0]
    item.apply_match(
        MatchResult(
            target_ref=ExternalTrackRef(Platform.SOUNDCLOUD, "9"),
            method="isrc",
            score=MatchScore(1.0),
        )
    )
    transfer.pull_domain_events()

    transfer.record_item_outcome(item, _NOW)

    assert [type(e) for e in transfer.pull_domain_events()] == [TrackMatched]


@pytest.mark.parametrize("status", [TransferStatus.REVIEW, TransferStatus.DONE])
def test_pause_not_allowed_outside_platform_phases(status: TransferStatus) -> None:
    transfer = _running()
    transfer.status = status

    with pytest.raises(InvalidTransferTransitionError):
        transfer.pause_for_client("offline", _NOW)


def test_resume_requires_client_pause() -> None:
    transfer = _running()

    with pytest.raises(InvalidTransferTransitionError):
        transfer.resume_after_client(_NOW)
