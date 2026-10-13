from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from syncplaylists.modules.recognition.domain.entities import Recognition
from syncplaylists.modules.recognition.domain.fragments import FragmentWindow, SourceAudio
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.search import RecognizedTrack
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform


class FakeAudioSource:
    """Отдаёт заданные байты или бросает заданную ошибку; помнит запросы."""

    def __init__(
        self, platform: Platform = Platform.SOUNDCLOUD, error: Exception | None = None
    ) -> None:
        self.platform = platform
        self.error = error
        self.calls: list[tuple[ExternalTrackRef, int, int | None]] = []

    async def fetch(
        self, ref: ExternalTrackRef, length_ms: int, duration_ms: int | None
    ) -> SourceAudio:
        self.calls.append((ref, length_ms, duration_ms))
        if self.error is not None:
            raise self.error
        return SourceAudio(b"audio", FragmentWindow.middle(duration_ms, length_ms))


class FakeAudioSourceRegistry:
    def __init__(self, source: FakeAudioSource | None = None) -> None:
        self.source = source

    def supports(self, platform: Platform) -> bool:
        return self.source is not None and self.source.platform is platform

    def for_track(
        self, ref: ExternalTrackRef, access: AccountAccess | None
    ) -> FakeAudioSource | None:
        return self.source if self.supports(ref.platform) else None


class FakeTrimmer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[SourceAudio] = []

    async def to_wav(self, audio: SourceAudio) -> bytes:
        self.calls.append(audio)
        if self.error is not None:
            raise self.error
        return b"wav:" + audio.data


class FakeRecognizer:
    """Отвечает по очереди заданными результатами (трек, None или исключение)."""

    def __init__(self, provider: str, *answers: RecognizedTrack | Exception | None) -> None:
        self.provider = provider
        self._answers = list(answers)
        self.calls: list[bytes] = []

    async def recognize(self, wav: bytes) -> RecognizedTrack | None:
        self.calls.append(wav)
        answer = self._answers.pop(0) if self._answers else None
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeRecognitionRepository:
    def __init__(self) -> None:
        self.storage: dict[ExternalTrackRef, Recognition] = {}
        self.saved: list[Recognition] = []

    async def find(self, source_ref: ExternalTrackRef) -> Recognition | None:
        return self.storage.get(source_ref)

    async def save(self, recognition: Recognition) -> None:
        self.saved.append(recognition)
        self.storage[recognition.source_ref] = recognition


class FakeRecognitionLock:
    def __init__(self, busy: bool = False) -> None:
        self.busy = busy
        self.held: list[ExternalTrackRef] = []

    def hold(self, source_ref: ExternalTrackRef) -> AbstractAsyncContextManager[bool]:
        @asynccontextmanager
        async def _hold() -> AsyncIterator[bool]:
            if self.busy:
                yield False
                return
            self.held.append(source_ref)
            yield True

        return _hold()
