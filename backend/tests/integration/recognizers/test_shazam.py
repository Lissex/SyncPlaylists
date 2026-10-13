"""Shazam на ответах, записанных в спайке 6-0 (tests/tools/shazam_probe.py): ремикс узнан
как оригинал без сдвига, slowed + reverb — посторонний трек со сдвигом −4,4 % (11j)."""

import json

import httpx
import pytest
import respx

from syncplaylists.integrations.recognizers.shazam import (
    ShazamRecognizer,
    ShazamSignature,
    parse_shazam,
)
from syncplaylists.modules.recognition.domain.errors import (
    RecognizerBusyError,
    RecognizerUnavailableError,
)
from syncplaylists.shared_kernel.domain.value_objects import ISRC
from tests.integration.recognizers.conftest import FakeThrottle, fixture

_URL = "https://amp.shazam.com/discovery/v5/en-US/GB/iphone/-/tag/TAG/TAG"


class FakeSigner:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    async def sign(self, wav: bytes) -> ShazamSignature:
        self.calls.append(wav)
        return ShazamSignature(
            uri="data:audio/vnd.shazam.sig;base64,AAAA", samplems=12000, timestamp=1
        )


def _recognizer(
    http: httpx.AsyncClient, throttle: FakeThrottle, signer: FakeSigner | None = None
) -> ShazamRecognizer:
    return ShazamRecognizer(
        http,
        signer or FakeSigner(),
        throttle,
        timeout_seconds=5,
        cooldown_seconds=600,
        tag_id=lambda: "TAG",
    )


def test_parse_exact_recording() -> None:
    track = parse_shazam(fixture("shazam_recognized_remix.json"))

    assert track is not None
    assert (track.artist, track.title) == ("Juice WRLD", "Lucid Dreams")
    assert track.isrc == ISRC("USUG11800685")
    assert track.provider == "shazam"
    assert track.provider_ref == "407592725"
    assert track.exact
    assert track.offset_ms == 114_111


def test_parse_skewed_is_not_exact() -> None:
    # Slowed + reverb → посторонний трек, скорость и тон −4,4 %: только подсказка.
    track = parse_shazam(fixture("shazam_skewed_slowed.json"))

    assert track is not None
    assert (track.artist, track.title) == ("Slater", "Forget Me")
    assert track.isrc is None
    assert not track.exact
    assert track.time_skew == pytest.approx(-0.0438, abs=1e-3)


def test_parse_no_match() -> None:
    assert parse_shazam(fixture("shazam_no_match.json")) is None


def test_parse_invalid_isrc_dropped() -> None:
    data = fixture("shazam_recognized_remix.json")
    data["track"]["isrc"] = "not-an-isrc"

    track = parse_shazam(data)

    assert track is not None
    assert track.isrc is None


def test_parse_without_matches_is_not_exact() -> None:
    data = fixture("shazam_recognized_remix.json")
    data["matches"] = []

    track = parse_shazam(data)

    assert track is not None
    assert not track.exact


async def test_recognize_sends_signature_with_steady_headers(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    route = mock.post(url__startswith=_URL).respond(
        200, json=fixture("shazam_recognized_remix.json")
    )
    throttle = FakeThrottle()
    signer = FakeSigner()

    track = await _recognizer(http, throttle, signer).recognize(b"wav")

    assert track is not None
    assert track.title == "Lucid Dreams"
    assert throttle.acquired == ["shazam"]
    assert signer.calls == [b"wav"]
    request = route.calls.last.request
    assert json.loads(request.content)["signature"] == {
        "uri": "data:audio/vnd.shazam.sig;base64,AAAA",
        "samplems": 12000,
    }
    # Без маскировки: User-Agent — как у httpx, не подделка под приложение.
    assert request.headers["user-agent"].startswith("python-httpx/")


async def test_throttle_wait_too_long_is_busy_without_request(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    route = mock.post(url__startswith=_URL)
    signer = FakeSigner()

    with pytest.raises(RecognizerBusyError) as error:
        await _recognizer(http, FakeThrottle(wait=42), signer).recognize(b"wav")

    assert error.value.retry_after_seconds == 42
    assert not route.called
    assert signer.calls == []


async def test_429_pauses_whole_server_and_does_not_retry(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    route = mock.post(url__startswith=_URL).respond(429, headers={"Retry-After": "30"})
    throttle = FakeThrottle()

    with pytest.raises(RecognizerBusyError) as error:
        await _recognizer(http, throttle).recognize(b"wav")

    assert route.call_count == 1
    # Пауза — не меньше cooldown, даже если Retry-After короче.
    assert throttle.penalties == [("shazam", 600)]
    assert error.value.retry_after_seconds == 600


async def test_long_retry_after_wins_over_cooldown(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    mock.post(url__startswith=_URL).respond(429, headers={"Retry-After": "3600"})
    throttle = FakeThrottle()

    with pytest.raises(RecognizerBusyError):
        await _recognizer(http, throttle).recognize(b"wav")

    assert throttle.penalties == [("shazam", 3600)]


@pytest.mark.parametrize("status", [500, 503, 403])
async def test_http_error_is_unavailable(
    mock: respx.MockRouter, http: httpx.AsyncClient, status: int
) -> None:
    mock.post(url__startswith=_URL).respond(status)
    throttle = FakeThrottle()

    with pytest.raises(RecognizerUnavailableError):
        await _recognizer(http, throttle).recognize(b"wav")

    assert throttle.penalties == []


async def test_network_error_is_unavailable(
    mock: respx.MockRouter, http: httpx.AsyncClient
) -> None:
    mock.post(url__startswith=_URL).mock(side_effect=httpx.ConnectError("нет сети"))

    with pytest.raises(RecognizerUnavailableError):
        await _recognizer(http, FakeThrottle()).recognize(b"wav")


async def test_non_json_is_unavailable(mock: respx.MockRouter, http: httpx.AsyncClient) -> None:
    mock.post(url__startswith=_URL).respond(200, text="<html>")

    with pytest.raises(RecognizerUnavailableError):
        await _recognizer(http, FakeThrottle()).recognize(b"wav")
