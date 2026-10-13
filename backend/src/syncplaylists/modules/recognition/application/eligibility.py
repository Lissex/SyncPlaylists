from syncplaylists.modules.recognition.domain.policy import RecognitionPolicy
from syncplaylists.shared_kernel.domain.search import TrackCandidate


class RecognitionEligibility:
    """Публичная проверка «отправлять ли трек на распознавание» для других контекстов
    (transfers): политика recognition.domain плюс то, что распознавание вообще включено
    и есть чем распознавать."""

    def __init__(self, policy: RecognitionPolicy, *, enabled: bool) -> None:
        self._policy = policy
        self._enabled = enabled

    def reason_to_skip(
        self, source: TrackCandidate, *, opted_in: bool, speed_altered: bool
    ) -> str | None:
        """None — распознавать; иначе причина (Ineligibility.value)."""
        if not self._enabled:
            return "disabled"
        reason = self._policy.check(
            platform=source.ref.platform,
            duration_ms=source.duration.milliseconds if source.duration else None,
            has_isrc=source.isrc is not None,
            speed_altered=speed_altered,
            opted_in=opted_in,
        )
        return reason.value if reason is not None else None
