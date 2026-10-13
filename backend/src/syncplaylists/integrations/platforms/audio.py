from collections.abc import Callable, Mapping

from syncplaylists.modules.recognition.application.ports import AudioSource
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform

# Сборщик источника звука площадки: по аккаунту пользователя (у Яндекса звук — по
# токену) или без него (SoundCloud — анонимно). None — без аккаунта звука не взять.
AudioSourceBuilder = Callable[[AccountAccess | None], AudioSource | None]


class PlatformAudioSources:
    """Реализация recognition.AudioSourceRegistry. Какие площадки отдают звук, решает
    container.py (ARCHITECTURE.md, 9 и 11j: SoundCloud, Яндекс; VK и Spotify — нет)."""

    def __init__(self, builders: Mapping[Platform, AudioSourceBuilder]) -> None:
        self._builders = dict(builders)

    def supports(self, platform: Platform) -> bool:
        return platform in self._builders

    def for_track(self, ref: ExternalTrackRef, access: AccountAccess | None) -> AudioSource | None:
        builder = self._builders.get(ref.platform)
        return builder(access) if builder is not None else None
