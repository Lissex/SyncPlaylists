from typing import Any
from uuid import UUID

from syncplaylists.modules.recognition.domain.entities import Recognition, RecognitionOutcome
from syncplaylists.modules.recognition.domain.errors import NoAudioReason
from syncplaylists.modules.recognition.domain.fragments import FragmentWindow
from syncplaylists.modules.recognition.infrastructure.orm import RecognitionOrm
from syncplaylists.shared_kernel.domain.search import RecognizedTrack
from syncplaylists.shared_kernel.domain.value_objects import ISRC, ExternalTrackRef


def recognition_values(recognition: Recognition, source_pt_id: UUID) -> dict[str, Any]:
    """Значения строки recognitions — для INSERT ... ON CONFLICT DO UPDATE."""
    track = recognition.track
    window = recognition.window
    return {
        "id": recognition.id,
        "source_pt_id": source_pt_id,
        "outcome": recognition.outcome.value,
        "no_audio_reason": (
            recognition.no_audio_reason.value if recognition.no_audio_reason else None
        ),
        "provider": track.provider if track else None,
        "title": track.title if track else None,
        "artist": track.artist if track else None,
        "isrc": track.isrc.value if track and track.isrc else None,
        "provider_ref": track.provider_ref if track else None,
        "exact": track.exact if track else False,
        "offset_ms": track.offset_ms if track else None,
        "time_skew": track.time_skew if track else None,
        "frequency_skew": track.frequency_skew if track else None,
        "window_start_ms": window.start_ms if window else None,
        "window_length_ms": window.length_ms if window else None,
        "created_at": recognition.created_at,
        "expires_at": recognition.expires_at,
    }


def recognition_to_domain(orm: RecognitionOrm, source_ref: ExternalTrackRef) -> Recognition:
    outcome = RecognitionOutcome(orm.outcome)
    track = None
    if outcome is RecognitionOutcome.RECOGNIZED:
        assert orm.title is not None
        assert orm.artist is not None
        assert orm.provider is not None
        track = RecognizedTrack(
            title=orm.title,
            artist=orm.artist,
            provider=orm.provider,
            isrc=ISRC(orm.isrc) if orm.isrc else None,
            provider_ref=orm.provider_ref,
            exact=orm.exact,
            offset_ms=orm.offset_ms,
            time_skew=orm.time_skew,
            frequency_skew=orm.frequency_skew,
        )
    window = (
        FragmentWindow(orm.window_start_ms, orm.window_length_ms)
        if orm.window_start_ms is not None and orm.window_length_ms is not None
        else None
    )
    return Recognition(
        id=orm.id,
        source_ref=source_ref,
        outcome=outcome,
        created_at=orm.created_at,
        expires_at=orm.expires_at,
        track=track,
        window=window,
        no_audio_reason=NoAudioReason(orm.no_audio_reason) if orm.no_audio_reason else None,
    )
