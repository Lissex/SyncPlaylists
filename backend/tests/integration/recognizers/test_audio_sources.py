"""Источники звука на ответах формы спайка 6-0 (ARCHITECTURE.md 11j): SoundCloud анонимно,
Яндекс по токену. Качается только окно; шифрованное — NO_AUDIO без запросов сегментов;
хосты — только площадки."""

import hashlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import respx
from yandex_music import ClientAsync
from yandex_music.download_info import SIGN_SALT

from syncplaylists.integrations.platforms.media import MediaFetcher, byte_range_for, parse_hls
from syncplaylists.integrations.platforms.soundcloud import audio as sc_audio
from syncplaylists.integrations.platforms.soundcloud.client_id import ClientIdProvider
from syncplaylists.integrations.platforms.soundcloud.transport import (
    V2_BASE_URL,
    SoundCloudTransport,
)
from syncplaylists.integrations.platforms.yandex import audio as ya_audio
from syncplaylists.integrations.platforms.yandex.transport import HttpxYandexRequest
from syncplaylists.modules.recognition.domain.errors import (
    AudioTemporarilyUnavailableError,
    AudioUnavailableError,
    NoAudioReason,
)
from syncplaylists.modules.recognition.domain.fragments import FragmentWindow
from syncplaylists.shared_kernel.domain.value_objects import ExternalTrackRef, Platform

_SC_REF = ExternalTrackRef(Platform.SOUNDCLOUD, "1057094605")
_SC_TRACK_URL = f"{V2_BASE_URL}/tracks/1057094605"
_MEDIA = "https://api-v2.soundcloud.com/media/soundcloud:tracks:1057094605"


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(follow_redirects=False) as client:
        yield client


def _transcoding(
    kind: str, protocol: str, mime: str, *, snipped: bool = False, ms: int = 240_483
) -> dict[str, Any]:
    return {
        "url": f"{_MEDIA}/{kind}/stream/{protocol}",
        "preset": kind,
        "duration": ms,
        "snipped": snipped,
        "format": {"protocol": protocol, "mime_type": mime},
        "quality": "sq",
    }


def _track(*transcodings: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": 1057094605,
        "duration": 240_483,
        "track_authorization": "track-auth",
        "media": {"transcodings": list(transcodings)},
    }


_ENCRYPTED = _transcoding("aac_160k", "cbc-encrypted-hls", 'audio/mp4; codecs="mp4a.40.2"')
_ABR = _transcoding("abr_sq", "hls", "audio/mpegurl")
_HLS_MP3 = _transcoding("mp3_0_1", "hls", "audio/mpeg")
_PROGRESSIVE = _transcoding("mp3_1_0", "progressive", "audio/mpeg")


def _m3u8(*durations: float, key: str | None = None, host: str = "cf-hls-media.sndcdn.com") -> str:
    lines = ["#EXTM3U", "#EXT-X-VERSION:6", "#EXT-X-TARGETDURATION:10"]
    if key:
        lines.append(f'#EXT-X-KEY:METHOD={key},URI="https://cf-hls-media.sndcdn.com/key"')
    for index, duration in enumerate(durations):
        lines += [f"#EXTINF:{duration},", f"https://{host}/seg/{index}.mp3"]
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines)


def _soundcloud(
    http: httpx.AsyncClient, max_bytes: int = 1_500_000
) -> sc_audio.SoundCloudAudioSource:
    client_ids = ClientIdProvider(
        http, None, ttl_seconds=60, min_refresh_seconds=60, timeout_seconds=5, override="CID"
    )
    transport = SoundCloudTransport(
        http, base_url=V2_BASE_URL, timeout_seconds=5, client_ids=client_ids
    )
    media = MediaFetcher(
        http, allowed_hosts=sc_audio.ALLOWED_HOSTS, timeout_seconds=5, max_bytes=max_bytes
    )
    return sc_audio.SoundCloudAudioSource(transport, media)


# ---------------- разбор HLS и окна


def test_parse_hls_segments_and_window() -> None:
    playlist = parse_hls(_m3u8(10, 10, 10, 10), "https://cf-hls-media.sndcdn.com/playlist.m3u8")

    assert not playlist.encrypted
    assert playlist.duration_ms == 40_000
    covering = playlist.covering(FragmentWindow(11_000, 18_000))
    assert [s.start_ms for s in covering] == [10_000, 20_000]


@pytest.mark.parametrize("method", ["AES-128", "SAMPLE-AES"])
def test_parse_hls_any_key_is_encrypted(method: str) -> None:
    assert parse_hls(_m3u8(10, key=method), "https://x.sndcdn.com/p.m3u8").encrypted


def test_parse_hls_key_none_is_not_encrypted() -> None:
    assert not parse_hls(_m3u8(10, key="NONE"), "https://x.sndcdn.com/p.m3u8").encrypted


def test_byte_range_with_margin_and_lead() -> None:
    # 240 с по 16 000 Б/с; окно 111–129 с, запас 2 с с каждой стороны.
    first, last, lead_ms = byte_range_for(FragmentWindow(111_000, 18_000), 240_000, 3_840_000)

    assert (first, last) == (1_744_000, 2_096_000)
    assert lead_ms == 2_000


# ---------------- SoundCloud


async def test_soundcloud_hls_mp3_downloads_only_window_segments(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    mock.get(_SC_TRACK_URL).respond(200, json=_track(_ENCRYPTED, _ABR, _HLS_MP3))
    abr = mock.get(f"{_MEDIA}/abr_sq/stream/hls").respond(404, json={})
    stream = mock.get(f"{_MEDIA}/mp3_0_1/stream/hls").respond(
        200, json={"url": "https://cf-hls-media.sndcdn.com/playlist/x.m3u8?Policy=p"}
    )
    mock.get("https://cf-hls-media.sndcdn.com/playlist/x.m3u8").respond(
        200, text=_m3u8(*([10.0] * 24), 0.483)
    )
    segments = {
        i: mock.get(f"https://cf-hls-media.sndcdn.com/seg/{i}.mp3").respond(
            200, content=f"[{i}]".encode()
        )
        for i in range(25)
    }

    audio = await _soundcloud(http).fetch(_SC_REF, 18_000, 240_483)

    # Окно 111,2–129,2 с → сегменты 11 и 12 (по 10 с), остальные не качаются.
    assert audio.data == b"[11][12]"
    assert audio.window == FragmentWindow(111_241, 18_000)
    assert audio.lead_ms == 1_241
    assert [i for i, route in segments.items() if route.called] == [11, 12]
    assert not abr.called  # abr ниже mp3 по приоритету
    assert stream.calls.last.request.url.params["track_authorization"] == "track-auth"
    assert stream.calls.last.request.url.params["client_id"] == "CID"


async def test_soundcloud_progressive_uses_range(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    mock.get(_SC_TRACK_URL).respond(200, json=_track(_HLS_MP3, _PROGRESSIVE))
    mock.get(f"{_MEDIA}/mp3_1_0/stream/progressive").respond(
        200, json={"url": "https://cf-media.sndcdn.com/abc.128.mp3?Policy=p"}
    )
    total = 3_847_728  # 240 483 мс по 16 000 Б/с

    def file(request: httpx.Request) -> httpx.Response:
        if request.headers["Range"] == "bytes=0-0":
            return httpx.Response(
                206, content=b"x", headers={"Content-Range": f"bytes 0-0/{total}"}
            )
        return httpx.Response(206, content=b"window")

    route = mock.get("https://cf-media.sndcdn.com/abc.128.mp3").mock(side_effect=file)

    audio = await _soundcloud(http).fetch(_SC_REF, 18_000, 240_483)

    assert audio.data == b"window"
    ranges = [call.request.headers["Range"] for call in route.calls]
    first, last, _ = byte_range_for(FragmentWindow(111_241, 18_000), 240_483, total)
    assert ranges == ["bytes=0-0", f"bytes={first}-{last}"]


async def test_soundcloud_label_upload_only_encrypted_is_no_audio(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    mock.get(_SC_TRACK_URL).respond(200, json=_track(_ENCRYPTED))

    with pytest.raises(AudioUnavailableError) as error:
        await _soundcloud(http).fetch(_SC_REF, 18_000, 240_483)

    assert error.value.reason is NoAudioReason.ENCRYPTED


async def test_soundcloud_encrypted_m3u8_never_fetches_segments_or_key(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    mock.get(_SC_TRACK_URL).respond(200, json=_track(_HLS_MP3))
    mock.get(f"{_MEDIA}/mp3_0_1/stream/hls").respond(
        200, json={"url": "https://cf-hls-media.sndcdn.com/playlist/x.m3u8"}
    )
    mock.get("https://cf-hls-media.sndcdn.com/playlist/x.m3u8").respond(
        200, text=_m3u8(10, 10, key="AES-128")
    )
    segment = mock.get(url__startswith="https://cf-hls-media.sndcdn.com/seg/")
    key = mock.get("https://cf-hls-media.sndcdn.com/key")

    with pytest.raises(AudioUnavailableError) as error:
        await _soundcloud(http).fetch(_SC_REF, 18_000, 20_000)

    assert error.value.reason is NoAudioReason.ENCRYPTED
    assert not segment.called
    assert not key.called


async def test_soundcloud_foreign_host_is_not_requested(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    mock.get(_SC_TRACK_URL).respond(200, json=_track(_PROGRESSIVE))
    mock.get(f"{_MEDIA}/mp3_1_0/stream/progressive").respond(
        200, json={"url": "https://evil.example.com/file.mp3"}
    )
    evil = mock.get(url__startswith="https://evil.example.com/")

    with pytest.raises(AudioUnavailableError, match="allowlist"):
        await _soundcloud(http).fetch(_SC_REF, 18_000, 240_483)

    assert not evil.called


async def test_soundcloud_snip_preview_window_from_preview_middle(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    snip = _transcoding("mp3_0_1", "hls", "audio/mpeg", snipped=True, ms=30_000)
    mock.get(_SC_TRACK_URL).respond(200, json=_track(snip))
    mock.get(f"{_MEDIA}/mp3_0_1/stream/hls").respond(
        200, json={"url": "https://cf-hls-media.sndcdn.com/playlist/p.m3u8"}
    )
    mock.get("https://cf-hls-media.sndcdn.com/playlist/p.m3u8").respond(200, text=_m3u8(10, 10, 10))
    for i in range(3):
        mock.get(f"https://cf-hls-media.sndcdn.com/seg/{i}.mp3").respond(200, content=b"s")

    audio = await _soundcloud(http).fetch(_SC_REF, 18_000, 240_483)

    assert audio.preview
    assert audio.window == FragmentWindow(6_000, 18_000)


async def test_soundcloud_window_over_byte_limit(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    mock.get(_SC_TRACK_URL).respond(200, json=_track(_HLS_MP3))
    mock.get(f"{_MEDIA}/mp3_0_1/stream/hls").respond(
        200, json={"url": "https://cf-hls-media.sndcdn.com/playlist/x.m3u8"}
    )
    mock.get("https://cf-hls-media.sndcdn.com/playlist/x.m3u8").respond(200, text=_m3u8(10, 10, 10))
    for i in range(3):
        mock.get(f"https://cf-hls-media.sndcdn.com/seg/{i}.mp3").respond(200, content=b"x" * 600)

    with pytest.raises(AudioUnavailableError) as error:
        await _soundcloud(http, max_bytes=1_000).fetch(_SC_REF, 18_000, 30_000)

    assert error.value.reason is NoAudioReason.TOO_LARGE


@pytest.mark.parametrize(
    ("status", "error"), [(404, AudioUnavailableError), (503, AudioTemporarilyUnavailableError)]
)
async def test_soundcloud_track_errors(
    mock: respx.MockRouter, http: httpx.AsyncClient, status: int, error: type[Exception]
) -> None:
    mock.get(_SC_TRACK_URL).respond(status)

    with pytest.raises(error):
        await _soundcloud(http).fetch(_SC_REF, 18_000, 240_483)


# ---------------- Яндекс

_YA_REF = ExternalTrackRef(Platform.YANDEX, "33311009:3192570")
_XML_URL = "https://api.music.yandex.net/get-mp3/xml-ticket"
_XML = (
    "<download-info><host>api.music.yandex.net</host><path>/opaque/file</path>"
    "<ts>1a131a24078</ts><region>-1</region><s>salt-s</s></download-info>"
)


def _download_info(*variants: dict[str, Any]) -> dict[str, Any]:
    return {
        "invocationInfo": {
            "hostname": "music-stable-back",
            "req-id": "r",
            "exec-duration-millis": 1,
        },
        "result": list(variants),
    }


def _variant(codec: str, kbps: int, *, preview: bool = False) -> dict[str, Any]:
    return {
        "codec": codec,
        "bitrateInKbps": kbps,
        "gain": False,
        "preview": preview,
        "downloadInfoUrl": _XML_URL,
        "direct": False,
    }


def _yandex(http: httpx.AsyncClient) -> ya_audio.YandexAudioSource:
    client = ClientAsync("ya-token", request=HttpxYandexRequest(http, timeout_seconds=5))
    media = MediaFetcher(
        http, allowed_hosts=ya_audio.ALLOWED_HOSTS, timeout_seconds=5, max_bytes=1_500_000
    )
    return ya_audio.YandexAudioSource(client, "ya-token", media)


def test_yandex_direct_link_signed_like_library() -> None:
    sign = hashlib.md5((SIGN_SALT + "opaque/file" + "salt-s").encode()).hexdigest()

    assert ya_audio.direct_link(_XML) == (
        f"https://api.music.yandex.net/get-mp3/{sign}/1a131a24078/opaque/file"
    )


async def test_yandex_one_range_request_through_redirect(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    mock.get("https://api.music.yandex.net/tracks/33311009:3192570/download-info").respond(
        200, json=_download_info(_variant("mp3", 192), _variant("mp3", 64), _variant("aac", 64))
    )
    xml = mock.get(_XML_URL).respond(200, text=_XML, headers={"content-type": "application/xml"})
    link = mock.get(url__startswith="https://api.music.yandex.net/get-mp3/").respond(
        308, headers={"Location": "https://strm-rad-26.strm.yandex.net/file.mp3?x=1"}
    )
    storage = mock.get(url__startswith="https://strm-rad-26.strm.yandex.net/").respond(
        206, content=b"window"
    )

    audio = await _yandex(http).fetch(_YA_REF, 18_000, 239_836)

    assert audio.data == b"window"
    assert xml.calls.last.request.headers["Authorization"] == "OAuth ya-token"
    assert link.call_count == 1  # ссылка одноразовая — один запрос
    # Самый лёгкий вариант: mp3 64 кбит/с = 8 000 Б/с.
    first, last, _ = byte_range_for(
        FragmentWindow(110_918, 18_000), 239_836, 64 * 125 * 239_836 // 1000
    )
    assert storage.calls.last.request.headers["Range"] == f"bytes={first}-{last}"
    # Токен на хранилище не уходит (другой хост, свой Range).
    assert "Authorization" not in storage.calls.last.request.headers


async def test_yandex_preview_window(mock: respx.MockRouter, http: httpx.AsyncClient) -> None:
    mock.get("https://api.music.yandex.net/tracks/33311009:3192570/download-info").respond(
        200, json=_download_info(_variant("mp3", 128, preview=True))
    )
    mock.get(_XML_URL).respond(200, text=_XML)
    mock.get(url__startswith="https://api.music.yandex.net/get-mp3/").respond(206, content=b"p")

    audio = await _yandex(http).fetch(_YA_REF, 18_000, 239_836)

    assert audio.preview
    assert audio.window == FragmentWindow(6_000, 18_000)


async def test_yandex_no_variants_is_no_audio(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    mock.get("https://api.music.yandex.net/tracks/33311009:3192570/download-info").respond(
        200, json=_download_info()
    )

    with pytest.raises(AudioUnavailableError):
        await _yandex(http).fetch(_YA_REF, 18_000, 239_836)


async def test_yandex_captcha_is_temporary(mock: respx.MockRouter, http: httpx.AsyncClient) -> None:
    mock.get("https://api.music.yandex.net/tracks/33311009:3192570/download-info").respond(
        429, text="<html>captcha</html>", headers={"x-yandex-captcha": "1", "Retry-After": "600"}
    )

    with pytest.raises(AudioTemporarilyUnavailableError):
        await _yandex(http).fetch(_YA_REF, 18_000, 239_836)
