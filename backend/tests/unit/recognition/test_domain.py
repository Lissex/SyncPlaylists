from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from syncplaylists.modules.recognition.domain.entities import Recognition, RecognitionOutcome
from syncplaylists.modules.recognition.domain.errors import NoAudioReason
from syncplaylists.modules.recognition.domain.fragments import FragmentWindow
from syncplaylists.modules.recognition.domain.policy import Ineligibility, RecognitionPolicy
from syncplaylists.shared_kernel.domain.search import RecognizedTrack
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform

_REF = ExternalTrackRef(Platform.SOUNDCLOUD, "123")
_NOW = datetime(2026, 10, 12, tzinfo=UTC)


class TestFragmentWindow:
    def test_middle_of_track(self) -> None:
        window = FragmentWindow.middle(240_000, 18_000)
        assert (window.start_ms, window.end_ms) == (111_000, 129_000)

    def test_middle_of_preview(self) -> None:
        # SoundCloud Go+ отдаёт 30-секундное превью: окно — из его середины.
        assert FragmentWindow.middle(30_000, 18_000) == FragmentWindow(6_000, 18_000)

    @pytest.mark.parametrize("duration", [None, 10_000, 18_000])
    def test_short_or_unknown_from_start(self, duration: int | None) -> None:
        assert FragmentWindow.middle(duration, 18_000) == FragmentWindow(0, 18_000)

    @pytest.mark.parametrize(("start", "length"), [(-1, 1000), (0, 0)])
    def test_invalid(self, start: int, length: int) -> None:
        with pytest.raises(ValueError, match="Некорректное окно"):
            FragmentWindow(start, length)


class TestRecognition:
    def test_recognized_never_expires(self) -> None:
        track = RecognizedTrack(title="Lucid Dreams", artist="Juice WRLD", provider="shazam")
        recognition = Recognition.recognized(uuid4(), _REF, track, FragmentWindow(0, 18_000), _NOW)
        assert recognition.outcome is RecognitionOutcome.RECOGNIZED
        assert recognition.is_fresh(_NOW + timedelta(days=3650))

    def test_negative_outcomes_expire(self) -> None:
        no_match = Recognition.no_match(
            uuid4(), _REF, FragmentWindow(0, 18_000), _NOW, timedelta(days=30)
        )
        no_audio = Recognition.no_audio(
            uuid4(), _REF, NoAudioReason.ENCRYPTED, _NOW, timedelta(days=7)
        )
        assert no_match.is_fresh(_NOW + timedelta(days=29))
        assert not no_match.is_fresh(_NOW + timedelta(days=30))
        assert not no_audio.is_fresh(_NOW + timedelta(days=7))
        assert no_audio.no_audio_reason is NoAudioReason.ENCRYPTED

    def test_track_only_with_recognized(self) -> None:
        track = RecognizedTrack(title="x", artist="y", provider="shazam")
        with pytest.raises(ValueError, match="Распознанный трек"):
            Recognition(
                id=uuid4(),
                source_ref=_REF,
                outcome=RecognitionOutcome.NO_MATCH,
                created_at=_NOW,
                track=track,
            )
        with pytest.raises(ValueError, match="Распознанный трек"):
            Recognition(
                id=uuid4(), source_ref=_REF, outcome=RecognitionOutcome.RECOGNIZED, created_at=_NOW
            )

    def test_reason_only_with_no_audio(self) -> None:
        with pytest.raises(ValueError, match="Причина"):
            Recognition(
                id=uuid4(), source_ref=_REF, outcome=RecognitionOutcome.NO_AUDIO, created_at=_NOW
            )


class TestRecognitionPolicy:
    policy = RecognitionPolicy(
        audio_platforms=frozenset({Platform.SOUNDCLOUD, Platform.YANDEX}), min_track_ms=20_000
    )

    def _check(self, **overrides: object) -> Ineligibility | None:
        kwargs: dict[str, object] = {
            "platform": Platform.SOUNDCLOUD,
            "duration_ms": 200_000,
            "has_isrc": False,
            "speed_altered": False,
        }
        kwargs.update(overrides)
        return self.policy.check(**kwargs)  # type: ignore[arg-type]

    def test_eligible(self) -> None:
        assert self._check() is None
        assert self._check(duration_ms=None) is None

    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"opted_in": False}, Ineligibility.OPTED_OUT),
            ({"platform": Platform.VK}, Ineligibility.NO_AUDIO_SOURCE),
            ({"platform": Platform.SPOTIFY}, Ineligibility.NO_AUDIO_SOURCE),
            ({"has_isrc": True}, Ineligibility.HAS_ISRC),
            ({"speed_altered": True}, Ineligibility.SPEED_ALTERED),
            ({"duration_ms": 19_999}, Ineligibility.TOO_SHORT),
        ],
    )
    def test_ineligible(self, overrides: dict[str, object], reason: Ineligibility) -> None:
        assert self._check(**overrides) is reason
