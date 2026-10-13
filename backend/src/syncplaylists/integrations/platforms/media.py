"""Скачивание окна звука для распознавания (этап 6-3): Range-запросы и HLS-сегменты.

- Только хосты площадки из allowlist (суффиксы): редиректы — вручную, на чужой хост не
  идём (защита от SSRF — ссылки на поток приходят из ответов площадки).
- Не больше max_bytes на окно; сам файл трека целиком не качается.
- Зашифрованный HLS (EXT-X-KEY с METHOD≠NONE) — AudioUnavailableError(ENCRYPTED): ни
  сегменты, ни ключ не запрашиваются (решение владельца, ARCHITECTURE.md 11j).
- Звук живёт только в памяти вызова.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import httpx

from syncplaylists.modules.recognition.domain.errors import (
    AudioTemporarilyUnavailableError,
    AudioUnavailableError,
    NoAudioReason,
)
from syncplaylists.modules.recognition.domain.fragments import FragmentWindow

_MAX_REDIRECTS: Final = 3
_EXTINF: Final = re.compile(r"^#EXTINF:(?P<duration>[\d.]+)")
_MAP_URI: Final = re.compile(r'#EXT-X-MAP:.*URI="(?P<uri>[^"]+)"')
_KEY_METHOD: Final = re.compile(r"METHOD=(?P<method>[A-Z0-9-]+)")
# Запас вокруг окна при выборе байтов по оценке битрейта (CBR неточен, VBR — тем более).
RANGE_MARGIN_MS: Final = 2000


@dataclass(frozen=True, slots=True)
class HlsSegment:
    start_ms: int
    duration_ms: int
    url: str


@dataclass(frozen=True, slots=True)
class HlsPlaylist:
    segments: tuple[HlsSegment, ...]
    init_url: str | None  # EXT-X-MAP (fMP4) — его байты идут перед сегментами
    encrypted: bool

    @property
    def duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.segments)

    def covering(self, window: FragmentWindow) -> tuple[HlsSegment, ...]:
        return tuple(
            s
            for s in self.segments
            if s.start_ms + s.duration_ms > window.start_ms and s.start_ms < window.end_ms
        )


def parse_hls(text: str, base_url: str) -> HlsPlaylist:
    """Медиа-плейлист HLS. Шифрование — любой EXT-X-KEY с METHOD≠NONE, даже если
    зашифрована часть сегментов: поток с защитой не используем целиком (11j)."""
    if "#EXTM3U" not in text:
        raise AudioUnavailableError(NoAudioReason.NO_STREAM, "не m3u8")
    base = httpx.URL(base_url)
    encrypted = False
    init_url: str | None = None
    segments: list[HlsSegment] = []
    position = 0
    pending: int | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-KEY"):
            method = _KEY_METHOD.search(line)
            if method is None or method["method"] != "NONE":
                encrypted = True
        elif line.startswith("#EXT-X-MAP"):
            match = _MAP_URI.search(line)
            if match:
                init_url = str(base.join(match["uri"]))
        elif line.startswith("#EXTINF"):
            match = _EXTINF.match(line)
            pending = round(float(match["duration"]) * 1000) if match else 0
        elif not line.startswith("#") and pending is not None:
            segments.append(HlsSegment(position, pending, str(base.join(line))))
            position += pending
            pending = None
    return HlsPlaylist(tuple(segments), init_url, encrypted)


def byte_range_for(
    window: FragmentWindow, duration_ms: int, total_bytes: int
) -> tuple[int, int, int]:
    """Байты, покрывающие окно с запасом, по средней скорости потока: (первый, последний,
    lead_ms — где внутри скачанного начинается окно)."""
    start_ms = max(0, window.start_ms - RANGE_MARGIN_MS)
    end_ms = min(duration_ms, window.end_ms + RANGE_MARGIN_MS)
    first = total_bytes * start_ms // duration_ms
    last = min(total_bytes - 1, -(-total_bytes * end_ms // duration_ms))
    lead_ms = window.start_ms - duration_ms * first // total_bytes
    return first, last, max(0, lead_ms)


class MediaFetcher:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        allowed_hosts: Sequence[str],
        timeout_seconds: float,
        max_bytes: int,
    ) -> None:
        self._http = http
        self._allowed = tuple(allowed_hosts)
        self._timeout = timeout_seconds
        self.max_bytes = max_bytes

    def allowed(self, url: str) -> bool:
        parsed = httpx.URL(url)
        host = parsed.host
        return parsed.scheme == "https" and any(
            host == suffix.lstrip(".") or host.endswith(suffix) for suffix in self._allowed
        )

    async def text(self, url: str, headers: Mapping[str, str] | None = None) -> str:
        response = await self._get(url, headers or {})
        return response.text

    async def bytes_range(
        self, url: str, first: int, last: int, headers: Mapping[str, str] | None = None
    ) -> bytes:
        if last - first + 1 > self.max_bytes:
            raise AudioUnavailableError(NoAudioReason.TOO_LARGE, f"{last - first + 1} Б")
        response = await self._get(url, {**(headers or {}), "Range": f"bytes={first}-{last}"})
        return self._limited(response)

    async def total_size(self, url: str, headers: Mapping[str, str] | None = None) -> int:
        """Размер файла по Range на один байт (Content-Range: bytes 0-0/<размер>)."""
        response = await self._get(url, {**(headers or {}), "Range": "bytes=0-0"})
        total = response.headers.get("content-range", "").rpartition("/")[2]
        if not total.isdigit():
            raise AudioUnavailableError(NoAudioReason.NO_STREAM, "нет Content-Range")
        return int(total)

    async def whole(self, url: str) -> bytes:
        """Небольшой файл целиком (HLS-сегмент, init) — с тем же лимитом."""
        return self._limited(await self._get(url, {}))

    def _limited(self, response: httpx.Response) -> bytes:
        if len(response.content) > self.max_bytes:
            raise AudioUnavailableError(NoAudioReason.TOO_LARGE, f"{len(response.content)} Б")
        return response.content

    async def _get(self, url: str, headers: Mapping[str, str]) -> httpx.Response:
        for _ in range(_MAX_REDIRECTS + 1):
            if not self.allowed(url):
                raise AudioUnavailableError(
                    NoAudioReason.NO_STREAM, f"хост {httpx.URL(url).host} не из allowlist"
                )
            try:
                response = await self._http.get(url, headers=dict(headers), timeout=self._timeout)
            except httpx.TimeoutException as exc:
                raise AudioTemporarilyUnavailableError("поток: таймаут") from exc
            except httpx.HTTPError as exc:
                raise AudioTemporarilyUnavailableError(f"поток: {type(exc).__name__}") from exc
            if response.is_redirect:
                url = str(response.url.join(response.headers["location"]))
                continue
            if response.status_code in (200, 206):
                return response
            if response.status_code in (401, 403, 404, 410):
                raise AudioUnavailableError(
                    NoAudioReason.NO_STREAM, f"поток: HTTP {response.status_code}"
                )
            raise AudioTemporarilyUnavailableError(f"поток: HTTP {response.status_code}")
        raise AudioUnavailableError(NoAudioReason.NO_STREAM, "слишком много редиректов")
