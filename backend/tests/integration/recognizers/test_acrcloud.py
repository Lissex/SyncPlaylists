"""ACRCloud на ответах формы из документации Identification API: живого ключа на этапе
6-0 не было (ARCHITECTURE.md, 11j)."""

import base64
import hashlib
import hmac

import httpx
import pytest
import respx

from syncplaylists.integrations.recognizers.acrcloud import AcrCloudRecognizer, parse_acrcloud
from syncplaylists.modules.recognition.domain.errors import (
    RecognizerBusyError,
    RecognizerUnavailableError,
)
from syncplaylists.shared_kernel.domain.value_objects import ISRC
from tests.integration.recognizers.conftest import FakeThrottle, fixture

_URL = "https://identify-eu-west-1.acrcloud.com/v1/identify"


def _recognizer(http: httpx.AsyncClient, throttle: FakeThrottle) -> AcrCloudRecognizer:
    return AcrCloudRecognizer(
        http,
        throttle,
        host="identify-eu-west-1.acrcloud.com",
        access_key="key",
        access_secret="secret",
        timeout_seconds=5,
        clock=lambda: 1_791_830_000.0,
    )


def test_parse_recognized() -> None:
    track = parse_acrcloud(fixture("acrcloud_recognized.json"))

    assert track is not None
    assert (track.artist, track.title) == ("Juice WRLD", "Lucid Dreams")
    assert track.isrc == ISRC("USUG11800685")
    assert track.provider == "acrcloud"
    assert track.exact  # score 100
    assert track.offset_ms == 120_360


def test_parse_low_score_is_hint() -> None:
    data = fixture("acrcloud_recognized.json")
    data["metadata"]["music"][0]["score"] = 75

    track = parse_acrcloud(data)

    assert track is not None
    assert not track.exact


def test_parse_several_artists_and_isrc_list() -> None:
    data = fixture("acrcloud_recognized.json")
    music = data["metadata"]["music"][0]
    music["artists"] = [{"name": "Juice WRLD"}, {"name": "Lil Uzi Vert"}]
    music["external_ids"]["isrc"] = ["bad", "USUG11800685"]

    track = parse_acrcloud(data)

    assert track is not None
    assert track.artist == "Juice WRLD, Lil Uzi Vert"
    assert track.isrc == ISRC("USUG11800685")


def test_parse_no_result() -> None:
    assert parse_acrcloud(fixture("acrcloud_no_result.json")) is None


def test_parse_quota_is_busy() -> None:
    with pytest.raises(RecognizerBusyError) as error:
        parse_acrcloud(fixture("acrcloud_quota.json"))
    assert error.value.retry_after_seconds == 3600


def test_parse_other_error_is_unavailable() -> None:
    with pytest.raises(RecognizerUnavailableError, match="3014"):
        parse_acrcloud({"status": {"code": 3014, "msg": "invalid signature"}})


async def test_recognize_signs_request(mock: respx.MockRouter, http: httpx.AsyncClient) -> None:
    route = mock.post(_URL).respond(200, json=fixture("acrcloud_recognized.json"))
    throttle = FakeThrottle()

    track = await _recognizer(http, throttle).recognize(b"RIFFwav")

    assert track is not None
    assert throttle.acquired == ["acrcloud"]
    body = route.calls.last.request.content
    expected = base64.b64encode(
        hmac.new(b"secret", b"POST\n/v1/identify\nkey\naudio\n1\n1791830000", hashlib.sha1).digest()
    ).decode()
    assert expected.encode() in body
    assert b'name="sample"' in body
    assert b"RIFFwav" in body
    assert b'name="sample_bytes"\r\n\r\n7' in body


async def test_quota_pauses_provider(mock: respx.MockRouter, http: httpx.AsyncClient) -> None:
    mock.post(_URL).respond(200, json=fixture("acrcloud_quota.json"))
    throttle = FakeThrottle()

    with pytest.raises(RecognizerBusyError):
        await _recognizer(http, throttle).recognize(b"wav")

    assert throttle.penalties == [("acrcloud", 3600)]


async def test_throttle_wait_is_busy_without_request(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    route = mock.post(_URL)

    with pytest.raises(RecognizerBusyError):
        await _recognizer(http, FakeThrottle(wait=3)).recognize(b"wav")

    assert not route.called


async def test_http_error_is_unavailable(mock: respx.MockRouter, http: httpx.AsyncClient) -> None:
    mock.post(_URL).respond(502)

    with pytest.raises(RecognizerUnavailableError):
        await _recognizer(http, FakeThrottle()).recognize(b"wav")
