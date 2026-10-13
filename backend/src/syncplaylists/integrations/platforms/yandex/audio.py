"""Звук трека Яндекс Музыки для распознавания (этап 6-3) — по токену аккаунта, как
официальные клиенты: `tracks/{id}/download-info` → XML → ссылка get-mp3 (ARCHITECTURE.md 11j).

- XML по `downloadInfoUrl` отдаётся только с `Authorization` (без неё Яндекс рвёт
  соединение); подпись ссылки — как у DownloadInfo библиотеки yandex-music.
- Подписанная ссылка одноразовая (повторный запрос сервер обрывает): окно берётся ОДНИМ
  Range-запросом по оценке битрейта (CBR), без предварительного запроса размера.
- Все запросы — через общий token bucket Яндекса (аккаунт + сервер, 11g).
- Без подписки Яндекс отдаёт превью — окно из его середины.
- Транспорт расширения (без токена) звук пока не отдаёт — долг 6-3.
"""

import hashlib
import xml.dom.minidom as minidom
from typing import Final
from uuid import UUID

from yandex_music import ClientAsync
from yandex_music.download_info import SIGN_SALT

from syncplaylists.integrations.platforms.media import MediaFetcher, byte_range_for
from syncplaylists.integrations.platforms.yandex.ids import YandexTrackId
from syncplaylists.modules.recognition.domain.errors import (
    AudioTemporarilyUnavailableError,
    AudioUnavailableError,
    NoAudioReason,
)
from syncplaylists.modules.recognition.domain.fragments import FragmentWindow, SourceAudio
from syncplaylists.shared_kernel.application.ports import PlatformRateLimiter
from syncplaylists.shared_kernel.domain.errors import (
    PlatformAuthError,
    PlatformError,
    PlatformRateLimitedError,
    PlatformUnavailableError,
)
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform

# api.music.yandex.net отвечает 308 на strm-*.strm.yandex.net (спайк 6-0).
ALLOWED_HOSTS: Final = (".yandex.net", ".yandex.ru")
_PREVIEW_MS: Final = 30_000
_CODEC_ORDER: Final = ("mp3", "aac", "he-aac")


class YandexAudioSource:
    platform = Platform.YANDEX

    def __init__(
        self,
        client: ClientAsync,
        token: str,
        media: MediaFetcher,
        *,
        limiter: PlatformRateLimiter | None = None,
        account_id: UUID | None = None,
    ) -> None:
        self._client = client
        self._token = token
        self._media = media
        self._limiter = limiter
        self._account_id = account_id

    async def fetch(
        self, ref: ExternalTrackRef, length_ms: int, duration_ms: int | None
    ) -> SourceAudio:
        track_id = YandexTrackId.parse(ref.external_id)
        try:
            infos = await self._client.tracks_download_info(str(track_id))
        except (PlatformRateLimitedError, PlatformUnavailableError, PlatformAuthError) as exc:
            raise AudioTemporarilyUnavailableError(f"yandex: {exc}") from exc
        except PlatformError as exc:
            raise AudioUnavailableError(NoAudioReason.NO_STREAM, f"yandex: {exc}") from exc
        known = [i for i in infos or [] if i.codec in _CODEC_ORDER and i.bitrate_in_kbps]
        if not known:
            raise AudioUnavailableError(NoAudioReason.NO_STREAM, "нет вариантов загрузки")
        # Для распознавания хватает самого лёгкого варианта — меньше байт на окно.
        chosen = min(known, key=lambda i: (_CODEC_ORDER.index(i.codec), i.bitrate_in_kbps))
        auth = {"Authorization": f"OAuth {self._token}"}
        await self._acquire()
        xml = await self._media.text(chosen.download_info_url, auth)
        link = direct_link(xml)

        stream_ms = _PREVIEW_MS if chosen.preview else duration_ms
        if not stream_ms:
            raise AudioUnavailableError(NoAudioReason.NO_STREAM, "нет длительности трека")
        window = FragmentWindow.middle(stream_ms, length_ms)
        estimated_total = chosen.bitrate_in_kbps * 125 * stream_ms // 1000  # байт
        first, last, lead_ms = byte_range_for(window, stream_ms, estimated_total)
        await self._acquire()
        data = await self._media.bytes_range(link, first, last)
        return SourceAudio(data, window, lead_ms=lead_ms, preview=chosen.preview)

    async def _acquire(self) -> None:
        if self._limiter is None or self._account_id is None:
            return
        try:
            await self._limiter.acquire(Platform.YANDEX, self._account_id)
        except PlatformRateLimitedError as exc:
            raise AudioTemporarilyUnavailableError(f"yandex: {exc}") from exc


def direct_link(xml: str) -> str:
    """Ссылка на файл из XML download-info — та же подпись, что у DownloadInfo
    yandex-music (md5 от соли библиотеки, пути и `s`)."""
    try:
        doc = minidom.parseString(xml)  # ответ Яндекса с allowlist-хоста
    except Exception as exc:
        raise AudioUnavailableError(NoAudioReason.NO_STREAM, "download-info: не XML") from exc
    values: dict[str, str] = {}
    for tag in ("host", "path", "ts", "s"):
        nodes = doc.getElementsByTagName(tag)
        node = nodes[0].firstChild if nodes else None
        value = node.nodeValue if node is not None else None
        if not value:
            raise AudioUnavailableError(NoAudioReason.NO_STREAM, f"download-info: нет {tag}")
        values[tag] = value
    path = values["path"]
    sign = hashlib.md5((SIGN_SALT + path[1:] + values["s"]).encode()).hexdigest()
    return f"https://{values['host']}/get-mp3/{sign}/{values['ts']}{path}"
