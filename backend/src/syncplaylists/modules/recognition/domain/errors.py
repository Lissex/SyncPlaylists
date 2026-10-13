from enum import StrEnum


class NoAudioReason(StrEnum):
    """Почему у трека источника нет звука для распознавания — надолго (кэшируется)."""

    # Поток зашифрован (encrypted-hls, EXT-X-KEY с METHOD≠NONE, DRM). Не расшифровываем
    # никогда, даже если ключ доступен (ARCHITECTURE.md, 11j).
    ENCRYPTED = "encrypted"
    # Площадка не отдаёт потока: нет транскодингов, приватный трек, гео.
    NO_STREAM = "no_stream"
    # Окно не уложилось в лимит байтов (необычно высокий битрейт).
    TOO_LARGE = "too_large"


class RecognitionError(Exception):
    pass


class AudioUnavailableError(RecognitionError):
    """Звука нет и не будет — в кэш как NO_AUDIO."""

    def __init__(self, reason: NoAudioReason, detail: str | None = None) -> None:
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
        self.reason = reason


class AudioTemporarilyUnavailableError(RecognitionError):
    """Сеть, 5xx, лимит площадки, нет аккаунта или расширения — не кэшируем."""


class RecognizerBusyError(RecognitionError):
    """Распознаватель просит подождать: наш темп для него исчерпан или он ответил 429."""

    def __init__(self, provider: str, retry_after_seconds: float) -> None:
        super().__init__(f"{provider}: подождать {retry_after_seconds:.0f} с")
        self.provider = provider
        self.retry_after_seconds = retry_after_seconds


class RecognizerUnavailableError(RecognitionError):
    """Распознаватель не ответил (сеть, 5xx, неожиданный ответ)."""
