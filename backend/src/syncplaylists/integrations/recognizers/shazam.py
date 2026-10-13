"""Shazam: подпись фрагмента считает shazamio-core (Rust), запрос — наш httpx.

Почему не библиотека shazamio целиком (ARCHITECTURE.md, 11j): она повторяет запрос до 20
раз на 429/5xx и подставляет случайные User-Agent и устройство в каждый запрос. Это
расходится с правилом «без обхода ограничений» (11g). Здесь — постоянные заголовки,
User-Agent httpx как есть, ни одного повтора: 429 → пауза для всего сервера
(RecognizerThrottle.penalize) и RecognizerBusyError.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol
from uuid import uuid4

import httpx
from shazamio_core import Recognizer, SearchParams, SignatureError

from syncplaylists.modules.recognition.application.ports import RecognizerThrottle
from syncplaylists.modules.recognition.domain.errors import (
    RecognizerBusyError,
    RecognizerUnavailableError,
)
from syncplaylists.shared_kernel.domain.search import RecognizedTrack
from syncplaylists.shared_kernel.domain.value_objects import ISRC

logger = logging.getLogger(__name__)

PROVIDER: Final = "shazam"
_URL: Final = (
    "https://amp.shazam.com/discovery/v5/{language}/{country}/iphone/-/tag/{u1}/{u2}"
    "?sync=true&webv3=true&sampling=true&connected=&shazamapiversion=v3"
    "&sharehub=true&hubv5minorversion=v5.1&hidelb=true&video=v3"
)
_TIMEZONE: Final = "Europe/Moscow"
# Та же запись: у лучшего совпадения скорость и тон отличаются меньше чем на 1 %.
# Slowed + reverb в спайке дал −4,4 % и посторонний трек (11j).
EXACT_MAX_SKEW: Final = 0.01


@dataclass(frozen=True, slots=True)
class ShazamSignature:
    uri: str
    samplems: int
    timestamp: int


class ShazamSigner(Protocol):
    async def sign(self, wav: bytes) -> ShazamSignature: ...


class CoreShazamSigner:
    """Подпись по центральному куску wav длиной segment_seconds (shazamio-core)."""

    def __init__(self, segment_seconds: int) -> None:
        self._segment_seconds = segment_seconds

    async def sign(self, wav: bytes) -> ShazamSignature:
        # Recognizer создаётся на вызов: его методы возвращают Future текущего цикла.
        recognizer = Recognizer(segment_duration_seconds=self._segment_seconds)
        try:
            signature = await recognizer.recognize_bytes(
                wav, SearchParams(segment_duration_seconds=self._segment_seconds)
            )
        except SignatureError as exc:
            raise RecognizerUnavailableError(f"{PROVIDER}: подпись не посчиталась") from exc
        return ShazamSignature(
            uri=signature.signature.uri,
            samplems=signature.signature.samples,
            timestamp=signature.timestamp,
        )


class ShazamRecognizer:
    provider = PROVIDER

    def __init__(
        self,
        http: httpx.AsyncClient,
        signer: ShazamSigner,
        throttle: RecognizerThrottle,
        *,
        timeout_seconds: float,
        cooldown_seconds: float,
        language: str = "en-US",
        country: str = "GB",
        tag_id: Callable[[], str] = lambda: str(uuid4()).upper(),
    ) -> None:
        self._http = http
        self._signer = signer
        self._throttle = throttle
        self._timeout = timeout_seconds
        self._cooldown = cooldown_seconds
        self._language = language
        self._country = country
        self._tag_id = tag_id

    async def recognize(self, wav: bytes) -> RecognizedTrack | None:
        wait = await self._throttle.acquire(PROVIDER)
        if wait is not None:
            raise RecognizerBusyError(PROVIDER, wait)
        signature = await self._signer.sign(wav)
        url = _URL.format(
            language=self._language, country=self._country, u1=self._tag_id(), u2=self._tag_id()
        )
        body = {
            "timezone": _TIMEZONE,
            "signature": {"uri": signature.uri, "samplems": signature.samplems},
            "timestamp": signature.timestamp,
            "context": {},
            "geolocation": {},
        }
        headers = {
            "X-Shazam-Platform": "IPHONE",
            "X-Shazam-AppVersion": "14.1.0",
            "Accept": "application/json",
            "Accept-Language": self._language,
        }
        try:
            response = await self._http.post(url, json=body, headers=headers, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise RecognizerUnavailableError(f"{PROVIDER}: таймаут") from exc
        except httpx.HTTPError as exc:
            raise RecognizerUnavailableError(f"{PROVIDER}: {type(exc).__name__}") from exc
        if response.status_code == 429:
            pause = max(self._cooldown, _retry_after(response))
            logger.warning("Shazam ответил 429 — пауза распознавания %.0f с", pause)
            await self._throttle.penalize(PROVIDER, pause)
            raise RecognizerBusyError(PROVIDER, pause)
        if not response.is_success:
            raise RecognizerUnavailableError(f"{PROVIDER}: HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise RecognizerUnavailableError(f"{PROVIDER}: ответ не JSON") from exc
        return parse_shazam(data)


def parse_shazam(data: Any) -> RecognizedTrack | None:
    if not isinstance(data, dict):
        raise RecognizerUnavailableError(f"{PROVIDER}: неожиданный ответ")
    track = data.get("track")
    if not isinstance(track, dict):
        return None  # «не узнал»: matches пустой, track нет
    title, artist = track.get("title"), track.get("subtitle")
    if not isinstance(title, str) or not title or not isinstance(artist, str) or not artist:
        return None
    matches = data.get("matches")
    best = matches[0] if isinstance(matches, list) and matches else {}
    time_skew = _float(best.get("timeskew"))
    frequency_skew = _float(best.get("frequencyskew"))
    offset = _float(best.get("offset"))
    exact = (
        time_skew is not None
        and frequency_skew is not None
        and abs(time_skew) < EXACT_MAX_SKEW
        and abs(frequency_skew) < EXACT_MAX_SKEW
    )
    key = track.get("key")
    return RecognizedTrack(
        title=title,
        artist=artist,
        provider=PROVIDER,
        isrc=_isrc(track.get("isrc")),
        provider_ref=str(key) if key is not None else None,
        exact=exact,
        offset_ms=round(offset * 1000) if offset is not None else None,
        time_skew=time_skew,
        frequency_skew=frequency_skew,
    )


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _isrc(value: Any) -> ISRC | None:
    if not isinstance(value, str):
        return None
    try:
        return ISRC(value.strip().upper())
    except ValueError:
        return None


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(response.headers.get("retry-after", 0))
    except ValueError:
        return 0.0
