"""Звук трека SoundCloud для распознавания (этап 6-3) — анонимно, как сервер читает
публичное: api-v2 с client_id веб-клиента (ARCHITECTURE.md 11j).

- Транскодинги: progressive mp3 → HLS mp3 → HLS aac. Зашифрованные (`*-encrypted-hls`)
  не используем никогда — у заливок лейблов только они, это NO_AUDIO. `audio/mpegurl`
  (адаптивный abr) — не mp3, анонимно 404.
- Качается только окно: Range у progressive, сегменты окна у HLS. Хосты — CDN SoundCloud.
- SNIP (Go+) — 30-секундное превью: окно из его середины.
- Приватный трек (секретная ссылка) анонимно не читается — NO_AUDIO (долг: расширение).
"""

from typing import Any, Final

from syncplaylists.integrations.platforms.media import MediaFetcher, byte_range_for, parse_hls
from syncplaylists.integrations.platforms.soundcloud.ids import parse_track_id
from syncplaylists.integrations.platforms.soundcloud.transport import SoundCloudTransport
from syncplaylists.modules.recognition.domain.errors import (
    AudioTemporarilyUnavailableError,
    AudioUnavailableError,
    NoAudioReason,
)
from syncplaylists.modules.recognition.domain.fragments import FragmentWindow, SourceAudio
from syncplaylists.shared_kernel.domain.errors import (
    PlatformError,
    PlatformRateLimitedError,
    PlatformUnavailableError,
)
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform

# CDN потоков (спайк 6-0): cf-media / cf-hls-media.sndcdn.com,
# playback.media-streaming.soundcloud.cloud.
ALLOWED_HOSTS: Final = (".sndcdn.com", ".soundcloud.cloud")
_PROGRESSIVE: Final = "progressive"
_HLS: Final = "hls"


def _rank(transcoding: dict[str, Any]) -> int | None:
    fmt = transcoding.get("format") or {}
    protocol, mime = str(fmt.get("protocol")), str(fmt.get("mime_type"))
    if "encrypted" in protocol:
        return None
    if protocol == _PROGRESSIVE and mime == "audio/mpeg":
        return 0
    if protocol == _HLS and mime == "audio/mpeg":
        return 1
    if protocol == _HLS and mime.startswith("audio/mp4"):
        return 2
    return None


class SoundCloudAudioSource:
    platform = Platform.SOUNDCLOUD

    def __init__(self, api: SoundCloudTransport, media: MediaFetcher) -> None:
        self._api = api
        self._media = media

    async def fetch(
        self, ref: ExternalTrackRef, length_ms: int, duration_ms: int | None
    ) -> SourceAudio:
        track = await self._call(f"/tracks/{parse_track_id(ref.external_id)}")
        transcodings = [
            t for t in (track.get("media") or {}).get("transcodings") or [] if isinstance(t, dict)
        ]
        usable = sorted(
            ((rank, i) for i, t in enumerate(transcodings) if (rank := _rank(t)) is not None)
        )
        if not usable:
            if any(
                "encrypted" in str((t.get("format") or {}).get("protocol")) for t in transcodings
            ):
                raise AudioUnavailableError(NoAudioReason.ENCRYPTED, "только encrypted-hls")
            raise AudioUnavailableError(NoAudioReason.NO_STREAM, "нет транскодингов")
        params = (
            {"track_authorization": track["track_authorization"]}
            if track.get("track_authorization")
            else {}
        )
        last_error: AudioUnavailableError | None = None
        for _, index in usable:
            transcoding = transcodings[index]
            try:
                stream = await self._call(str(transcoding["url"]), params)
            except AudioUnavailableError as exc:
                last_error = exc  # этот вариант недоступен (abr, гео) — следующий
                continue
            url = stream.get("url") if isinstance(stream, dict) else None
            if not isinstance(url, str):
                continue
            stream_ms = (
                int(transcoding.get("duration") or track.get("duration") or 0) or duration_ms
            )
            preview = bool(transcoding.get("snipped"))
            window = FragmentWindow.middle(stream_ms, length_ms)
            protocol = (transcoding.get("format") or {}).get("protocol")
            if protocol == _PROGRESSIVE:
                return await self._progressive(url, window, stream_ms, preview)
            return await self._hls(url, window, preview)
        raise last_error or AudioUnavailableError(NoAudioReason.NO_STREAM, "поток не выдан")

    async def _progressive(
        self, url: str, window: FragmentWindow, stream_ms: int | None, preview: bool
    ) -> SourceAudio:
        if not stream_ms:
            raise AudioUnavailableError(NoAudioReason.NO_STREAM, "нет длительности потока")
        total = await self._media.total_size(url)
        first, last, lead_ms = byte_range_for(window, stream_ms, total)
        data = await self._media.bytes_range(url, first, last)
        return SourceAudio(data, window, lead_ms=lead_ms, preview=preview)

    async def _hls(self, url: str, window: FragmentWindow, preview: bool) -> SourceAudio:
        playlist = parse_hls(await self._media.text(url), url)
        if playlist.encrypted:
            raise AudioUnavailableError(NoAudioReason.ENCRYPTED, "EXT-X-KEY в m3u8")
        if playlist.duration_ms and window.end_ms > playlist.duration_ms:
            window = FragmentWindow.middle(playlist.duration_ms, window.length_ms)
        segments = playlist.covering(window)
        if not segments:
            raise AudioUnavailableError(NoAudioReason.NO_STREAM, "сегменты окна не найдены")
        parts = [await self._media.whole(playlist.init_url)] if playlist.init_url else []
        for segment in segments:
            parts.append(await self._media.whole(segment.url))
            if sum(map(len, parts)) > self._media.max_bytes:
                raise AudioUnavailableError(NoAudioReason.TOO_LARGE, "сегменты окна")
        lead_ms = window.start_ms - segments[0].start_ms
        return SourceAudio(b"".join(parts), window, lead_ms=lead_ms, preview=preview)

    async def _call(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            return await self._api.get(path, params)
        except (PlatformRateLimitedError, PlatformUnavailableError) as exc:
            raise AudioTemporarilyUnavailableError(f"soundcloud: {exc}") from exc
        except PlatformError as exc:
            # 401/403/404: приватный, удалённый, гео — анонимно звука нет.
            raise AudioUnavailableError(NoAudioReason.NO_STREAM, f"soundcloud: {exc}") from exc
