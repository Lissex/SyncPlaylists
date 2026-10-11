from datetime import UTC, datetime
from uuid import uuid4

import pytest

from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.errors import InvalidTransferTransitionError
from syncplaylists.modules.transfers.domain.events import (
    CaptchaRequired,
    TrackMatched,
    TrackNeedsReview,
    TrackNotFound,
    TransferCompleted,
    TransferFailed,
    TransferStarted,
    TransferWritingStarted,
)
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    MatchResult,
    PlaylistSource,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)

_NOW = datetime(2026, 10, 2, tzinfo=UTC)


def _transfer() -> Transfer:
    return Transfer(
        id=uuid4(),
        user_id=uuid4(),
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, "src-playlist")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst-playlist")),
    )


def _match_result(external_id: str = "tgt-1") -> MatchResult:
    return MatchResult(
        target_ref=ExternalTrackRef(Platform.SPOTIFY, external_id),
        method="fuzzy",
        score=MatchScore(0.95),
    )


def _candidate(external_id: str = "tgt-1") -> TrackCandidate:
    return TrackCandidate(
        ref=ExternalTrackRef(Platform.SPOTIFY, external_id), title="Starboy", artist="The Weeknd"
    )


def test_start_moves_to_running_and_emits_event() -> None:
    transfer = _transfer()

    transfer.start(_NOW)

    assert transfer.status.value == "running"
    events = transfer.pull_domain_events()
    assert len(events) == 1
    assert isinstance(events[0], TransferStarted)
    assert events[0].transfer_id == transfer.id


def test_start_twice_raises() -> None:
    transfer = _transfer()
    transfer.start(_NOW)

    with pytest.raises(InvalidTransferTransitionError):
        transfer.start(_NOW)


def test_add_item_requires_running() -> None:
    transfer = _transfer()

    with pytest.raises(InvalidTransferTransitionError):
        transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))


def test_record_match_transitions_item_and_emits_event() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.pull_domain_events()

    result = _match_result()
    transfer.record_match(0, result, _NOW)

    assert transfer.items[0].status.value == "matched"
    assert transfer.items[0].match == result
    events = transfer.pull_domain_events()
    assert len(events) == 1
    assert isinstance(events[0], TrackMatched)


def test_record_match_twice_on_same_item_raises() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_match(0, _match_result(), _NOW)

    with pytest.raises(InvalidTransferTransitionError):
        transfer.record_match(0, _match_result(), _NOW)


def test_record_uncertain_and_not_found_emit_their_events() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.add_item(1, ExternalTrackRef(Platform.VK, "src-2"))
    transfer.pull_domain_events()

    transfer.record_uncertain(0, (_candidate(),), _NOW)
    transfer.record_not_found(1, (), _NOW)

    assert transfer.items[0].status.value == "uncertain"
    assert transfer.items[1].status.value == "not_found"
    events = transfer.pull_domain_events()
    assert isinstance(events[0], TrackNeedsReview)
    assert isinstance(events[1], TrackNotFound)


def test_pause_and_resume_captcha() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.pull_domain_events()

    transfer.pause_for_captcha("vk-captcha", _NOW)
    assert transfer.status.value == "paused_captcha"
    events = transfer.pull_domain_events()
    assert isinstance(events[0], CaptchaRequired)

    transfer.resume()
    assert transfer.status.value == "running"


def test_enter_review_requires_no_pending_items() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))

    with pytest.raises(InvalidTransferTransitionError):
        transfer.enter_review()


def test_enter_review_requires_at_least_one_item_needing_review() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_match(0, _match_result(), _NOW)

    with pytest.raises(InvalidTransferTransitionError):
        transfer.enter_review()


def test_resolve_item_with_chosen_candidate_marks_matched() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_uncertain(0, (_candidate(),), _NOW)
    transfer.enter_review()

    transfer.resolve_item(0, _candidate().ref, _NOW)

    assert transfer.items[0].status.value == "matched"
    assert transfer.items[0].match is not None
    assert transfer.items[0].match.method == "manual"


def test_resolve_item_without_candidate_marks_skipped() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_not_found(0, (), _NOW)
    transfer.enter_review()

    transfer.resolve_item(0, None, _NOW)

    assert transfer.items[0].status is TransferItemStatus.SKIPPED


def test_resolving_last_item_keeps_review_until_explicit_write() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_uncertain(0, (_candidate(),), _NOW)
    transfer.enter_review()

    transfer.resolve_item(0, _candidate().ref, _NOW)

    assert transfer.status is TransferStatus.REVIEW


def test_decision_can_be_changed_while_in_review() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_uncertain(0, (_candidate("a"), _candidate("b")), _NOW)
    transfer.enter_review()

    transfer.resolve_item(0, None, _NOW)
    transfer.resolve_item(0, _candidate("a").ref, _NOW)
    transfer.resolve_item(0, _candidate("b").ref, _NOW)

    item = transfer.items[0]
    assert item.status is TransferItemStatus.MATCHED
    assert item.match is not None
    assert item.match.target_ref == _candidate("b").ref

    transfer.resolve_item(0, None, _NOW)
    assert item.status is TransferItemStatus.SKIPPED
    assert item.match is None


def test_auto_matched_item_cannot_be_resolved() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.add_item(1, ExternalTrackRef(Platform.VK, "src-2"))
    transfer.record_match(0, _match_result(), _NOW)
    transfer.record_not_found(1, (), _NOW)
    transfer.enter_review()

    with pytest.raises(InvalidTransferTransitionError):
        transfer.resolve_item(0, None, _NOW)


def test_resolve_rejects_track_from_another_platform() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_not_found(0, (), _NOW)
    transfer.enter_review()

    with pytest.raises(InvalidTransferTransitionError):
        transfer.resolve_item(0, ExternalTrackRef(Platform.YANDEX, "1"), _NOW)


def test_begin_writing_from_running_without_unresolved_items() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_match(0, _match_result(), _NOW)
    transfer.pull_domain_events()

    transfer.begin_writing(_NOW)

    assert transfer.status.value == "writing"
    events = transfer.pull_domain_events()
    assert isinstance(events[0], TransferWritingStarted)


def test_begin_writing_fails_with_unresolved_items() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_uncertain(0, (), _NOW)

    with pytest.raises(InvalidTransferTransitionError):
        transfer.begin_writing(_NOW)


def test_begin_writing_from_review_skips_undecided_uncertain_and_keeps_not_found() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.add_item(1, ExternalTrackRef(Platform.VK, "src-2"))
    transfer.record_uncertain(0, (_candidate(),), _NOW)
    transfer.record_not_found(1, (), _NOW)
    transfer.enter_review()

    transfer.begin_writing(_NOW)

    assert transfer.status is TransferStatus.WRITING
    assert transfer.items[0].status is TransferItemStatus.SKIPPED
    assert transfer.items[1].status is TransferItemStatus.NOT_FOUND


def test_complete_counts_skipped_and_not_found_separately() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    for position in range(3):
        transfer.add_item(position, ExternalTrackRef(Platform.VK, f"src-{position}"))
    transfer.record_match(0, _match_result(), _NOW)
    transfer.record_uncertain(1, (_candidate("x"),), _NOW)
    transfer.record_not_found(2, (), _NOW)
    transfer.enter_review()
    transfer.begin_writing(_NOW)
    transfer.mark_added(0)
    transfer.pull_domain_events()

    transfer.complete(_NOW)

    (event,) = transfer.pull_domain_events()
    assert isinstance(event, TransferCompleted)
    assert (event.total, event.added, event.failed, event.skipped, event.not_found) == (
        3,
        1,
        0,
        1,
        1,
    )


def test_begin_writing_from_review_after_all_resolved() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_uncertain(0, (_candidate(),), _NOW)
    transfer.enter_review()
    transfer.resolve_item(0, _candidate().ref, _NOW)

    transfer.begin_writing(_NOW)

    assert transfer.status.value == "writing"


def test_complete_requires_all_items_in_terminal_write_status() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_match(0, _match_result(), _NOW)
    transfer.begin_writing(_NOW)

    with pytest.raises(InvalidTransferTransitionError):
        transfer.complete(_NOW)


def test_complete_emits_report_with_added_and_skipped_counts() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.add_item(1, ExternalTrackRef(Platform.VK, "src-2"))
    transfer.record_match(0, _match_result("tgt-1"), _NOW)
    transfer.record_not_found(1, (), _NOW)
    transfer.enter_review()
    transfer.resolve_item(1, None, _NOW)  # не нашли кандидата -> SKIPPED
    transfer.begin_writing(_NOW)
    transfer.mark_added(0)
    transfer.pull_domain_events()

    transfer.complete(_NOW)

    assert transfer.status.value == "done"
    events = transfer.pull_domain_events()
    assert isinstance(events[0], TransferCompleted)
    assert events[0].total == 2
    assert events[0].added == 1
    assert events[0].failed == 0
    assert events[0].skipped == 1


def test_mark_added_requires_matched_item() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.VK, "src-1"))
    transfer.record_not_found(0, (), _NOW)
    transfer.enter_review()
    transfer.resolve_item(0, None, _NOW)  # -> SKIPPED, не MATCHED
    transfer.begin_writing(_NOW)

    with pytest.raises(InvalidTransferTransitionError):
        transfer.mark_added(0)


def test_fail_from_any_nonterminal_status_emits_event() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.pull_domain_events()

    transfer.fail("адаптер упал", _NOW)

    assert transfer.status.value == "failed"
    events = transfer.pull_domain_events()
    assert isinstance(events[0], TransferFailed)


def test_fail_on_already_terminal_transfer_raises() -> None:
    transfer = _transfer()
    transfer.start(_NOW)
    transfer.fail("адаптер упал", _NOW)

    with pytest.raises(InvalidTransferTransitionError):
        transfer.fail("ещё раз", _NOW)
