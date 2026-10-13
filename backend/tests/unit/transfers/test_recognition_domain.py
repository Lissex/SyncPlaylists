"""Этап 6-2: трек в распознавании по звуку — домен (ARCHITECTURE.md 11j)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from syncplaylists.modules.transfers.domain.entities import Transfer, TransferItem
from syncplaylists.modules.transfers.domain.errors import InvalidTransferTransitionError
from syncplaylists.modules.transfers.domain.events import TrackMatched, TrackNotFound
from syncplaylists.modules.transfers.domain.value_objects import (
    NOTE_NO_MATCH,
    NOTE_TIMEOUT,
    NOTE_UNAVAILABLE,
    ExistingPlaylist,
    ItemRecognition,
    MatchResult,
    PlaylistSource,
    RecognitionState,
    TransferItemStatus,
    TransferStatus,
)
from syncplaylists.shared_kernel.domain.search import RecognizedTrack, TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import (
    ExternalTrackRef,
    MatchScore,
    Platform,
    PlaylistRef,
)

_NOW = datetime(2026, 10, 12, tzinfo=UTC)
_HEARD = RecognizedTrack(title="Lucid Dreams", artist="Juice WRLD", provider="shazam", exact=True)


def _candidate(external_id: str) -> TrackCandidate:
    return TrackCandidate(
        ref=ExternalTrackRef(Platform.YANDEX, external_id),
        title="Lucid Dreams",
        artist="Juice WRLD",
    )


def _item() -> TransferItem:
    return TransferItem(
        id=uuid4(),
        transfer_id=uuid4(),
        position=0,
        source_track=ExternalTrackRef(Platform.SOUNDCLOUD, "1"),
    )


def _audio_match() -> MatchResult:
    return MatchResult(_candidate("audio").ref, method="audio", score=MatchScore(0.97))


class TestItemRecognition:
    def test_text_status_must_be_unresolved(self) -> None:
        with pytest.raises(ValueError, match="текстового итога"):
            ItemRecognition(RecognitionState.QUEUED, TransferItemStatus.MATCHED)

    def test_awaiting_keeps_item_pending_with_text_candidates(self) -> None:
        item = _item()

        item.await_recognition(TransferItemStatus.UNCERTAIN, (_candidate("text"),))

        assert item.status is TransferItemStatus.PENDING
        assert item.candidates == (_candidate("text"),)
        assert item.recognition == ItemRecognition(
            RecognitionState.QUEUED, TransferItemStatus.UNCERTAIN
        )

    def test_recognized_only_once(self) -> None:
        item = _item()
        item.await_recognition(TransferItemStatus.NOT_FOUND, ())

        with pytest.raises(InvalidTransferTransitionError, match="уже распознавался"):
            item.await_recognition(TransferItemStatus.NOT_FOUND, ())

    def test_found_by_audio(self) -> None:
        item = _item()
        item.await_recognition(TransferItemStatus.NOT_FOUND, (_candidate("text"),))
        item.recognition_ready(None, _HEARD)

        item.apply_audio_outcome(
            TransferItemStatus.MATCHED, _audio_match(), (_candidate("audio"),), "exact"
        )

        assert item.status is TransferItemStatus.MATCHED
        assert item.match == _audio_match()
        assert item.candidates == (_candidate("audio"), _candidate("text"))
        assert item.recognition is not None
        assert item.recognition.state is RecognitionState.FINAL
        assert item.recognition.note == "exact"
        assert item.recognition.heard == _HEARD

    def test_audio_never_worsens_text_result(self) -> None:
        item = _item()
        item.await_recognition(TransferItemStatus.UNCERTAIN, (_candidate("text"),))
        item.recognition_ready(None, _HEARD)

        item.apply_audio_outcome(TransferItemStatus.NOT_FOUND, None, (), "not_on_target")

        assert item.status is TransferItemStatus.UNCERTAIN
        assert item.candidates == (_candidate("text"),)

    def test_audio_hint_raises_not_found_to_uncertain(self) -> None:
        item = _item()
        item.await_recognition(TransferItemStatus.NOT_FOUND, ())
        item.recognition_ready(None, _HEARD)

        item.apply_audio_outcome(
            TransferItemStatus.UNCERTAIN, None, (_candidate("hint"),), "altered"
        )

        assert item.status is TransferItemStatus.UNCERTAIN

    def test_text_result_when_audio_did_not_help(self) -> None:
        item = _item()
        item.await_recognition(TransferItemStatus.NOT_FOUND, ())
        item.recognition_ready(NOTE_NO_MATCH, None)

        item.apply_text_result()

        assert item.status is TransferItemStatus.NOT_FOUND
        assert item.recognition is not None
        assert item.recognition.note == NOTE_NO_MATCH

    def test_outcome_requires_ready(self) -> None:
        item = _item()
        item.await_recognition(TransferItemStatus.NOT_FOUND, ())

        with pytest.raises(InvalidTransferTransitionError, match="не в состоянии ready"):
            item.apply_text_result()


def _review_with_unavailable(note: str = NOTE_TIMEOUT) -> Transfer:
    transfer = Transfer(
        id=uuid4(),
        user_id=uuid4(),
        source=PlaylistSource(ref=PlaylistRef(Platform.SOUNDCLOUD, "src")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.YANDEX, "dst")),
    )
    transfer.start(_NOW)
    transfer.add_item(0, ExternalTrackRef(Platform.SOUNDCLOUD, "1"))
    item = transfer.items[0]
    item.await_recognition(TransferItemStatus.NOT_FOUND, ())
    item.recognition_ready(note, None)
    item.apply_text_result()
    transfer.status = TransferStatus.REVIEW
    return transfer


class TestRetryOnReview:
    def test_retry_after_timeout(self) -> None:
        transfer = _review_with_unavailable(NOTE_TIMEOUT)

        transfer.request_recognition_retry(0)

        recognition = transfer.items[0].recognition
        assert recognition == ItemRecognition(RecognitionState.QUEUED, TransferItemStatus.NOT_FOUND)
        assert transfer.items[0].status is TransferItemStatus.NOT_FOUND

    @pytest.mark.parametrize("note", [NOTE_NO_MATCH, "no_audio", "altered"])
    def test_no_retry_when_repeat_would_not_help(self, note: str) -> None:
        transfer = _review_with_unavailable(note)

        with pytest.raises(InvalidTransferTransitionError, match="недоступен"):
            transfer.request_recognition_retry(0)

    def test_retry_only_on_review(self) -> None:
        transfer = _review_with_unavailable(NOTE_UNAVAILABLE)
        transfer.status = TransferStatus.WRITING

        with pytest.raises(InvalidTransferTransitionError):
            transfer.request_recognition_retry(0)

    def test_retried_and_found(self) -> None:
        transfer = _review_with_unavailable(NOTE_UNAVAILABLE)
        transfer.request_recognition_retry(0)
        transfer.items[0].recognition_ready(None, _HEARD)
        transfer.pull_domain_events()

        transfer.apply_retried_recognition(
            0, TransferItemStatus.MATCHED, _audio_match(), (_candidate("audio"),), "exact", _NOW
        )

        assert transfer.items[0].status is TransferItemStatus.MATCHED
        assert transfer.items[0].match == _audio_match()
        events = transfer.pull_domain_events()
        assert [type(e) for e in events] == [TrackMatched]

    def test_retried_and_still_not_found(self) -> None:
        transfer = _review_with_unavailable(NOTE_UNAVAILABLE)
        transfer.request_recognition_retry(0)
        transfer.items[0].recognition_ready(NOTE_NO_MATCH, None)

        transfer.apply_retried_text_result(0, None, _NOW)

        assert transfer.items[0].status is TransferItemStatus.NOT_FOUND
        assert [type(e) for e in transfer.pull_domain_events()][-1] is TrackNotFound
