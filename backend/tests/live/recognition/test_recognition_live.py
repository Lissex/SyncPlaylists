"""Распознавание по звуку на настоящем треке (этап 6): SoundCloud анонимно → окно 18 с →
ffmpeg → Shazam. Ничего не меняет ни в аккаунтах, ни в БД.

Только в контейнере (ffmpeg), с треком владельца из окружения:

    docker compose run --rm --no-deps -v "${PWD}/backend/tests:/app/tests" `
        -e RECOGNITION_LIVE_TRACK_URL="https://soundcloud.com/<автор>/<трек>" `
        -e RECOGNITION_LIVE_EXPECT="Артист — Название" `
        api pytest -m live -s -p no:cacheprovider tests/live/recognition

Один запрос к Shazam, без повторов. Временный каталог ffmpeg после теста пуст.
"""

import os
import re
import tempfile

import httpx
import pytest

from syncplaylists.integrations.platforms.media import MediaFetcher
from syncplaylists.integrations.platforms.soundcloud import audio as sc_audio
from syncplaylists.integrations.platforms.soundcloud.client_id import ClientIdProvider
from syncplaylists.integrations.platforms.soundcloud.transport import (
    V2_BASE_URL,
    SoundCloudTransport,
)
from syncplaylists.integrations.recognizers.ffmpeg import FfmpegTrimmer
from syncplaylists.integrations.recognizers.shazam import CoreShazamSigner, ShazamRecognizer
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform
from tests.live.conftest import LiveSettings

pytestmark = pytest.mark.live


class _NoThrottle:
    async def acquire(self, provider: str) -> float | None:
        return None

    async def penalize(self, provider: str, seconds: float) -> None:
        return None


def _normalized(text: str) -> str:
    # Только буквы и цифры: консоль Windows может потерять тире в аргументе `-e`.
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


async def test_owner_track_recognized(live_settings: LiveSettings) -> None:
    url, expect = live_settings.recognition_live_track_url, live_settings.recognition_live_expect
    if not url or not expect:
        pytest.skip("RECOGNITION_LIVE_TRACK_URL / RECOGNITION_LIVE_EXPECT не заданы")
    temp_root = tempfile.mkdtemp(prefix="recognize-live-")
    async with httpx.AsyncClient(follow_redirects=False) as http:
        client_ids = ClientIdProvider(
            http, None, ttl_seconds=3600, min_refresh_seconds=60, timeout_seconds=20
        )
        transport = SoundCloudTransport(
            http, base_url=V2_BASE_URL, timeout_seconds=20, client_ids=client_ids
        )
        track = await transport.get("/resolve", {"url": url})
        ref = ExternalTrackRef(Platform.SOUNDCLOUD, str(track["id"]))
        duration = int(track.get("full_duration") or track["duration"])
        media = MediaFetcher(
            http, allowed_hosts=sc_audio.ALLOWED_HOSTS, timeout_seconds=20, max_bytes=1_500_000
        )

        audio = await sc_audio.SoundCloudAudioSource(transport, media).fetch(ref, 18_000, duration)
        wav = await FfmpegTrimmer(temp_dir=temp_root, timeout_seconds=30).to_wav(audio)
        heard = await ShazamRecognizer(
            http,
            CoreShazamSigner(12),
            _NoThrottle(),
            timeout_seconds=20,
            cooldown_seconds=600,
        ).recognize(wav)

    print(f"\nокно {audio.window}, {len(audio.data)} Б, wav {len(wav)} Б → {heard}")
    assert os.listdir(temp_root) == []  # фрагмент не остался на диске
    assert heard is not None, "Shazam не узнал трек"
    assert _normalized(f"{heard.artist} - {heard.title}") == _normalized(expect)
