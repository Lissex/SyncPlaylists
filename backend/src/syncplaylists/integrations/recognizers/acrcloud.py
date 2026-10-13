"""ACRCloud — запасной распознаватель (Identification API, `POST /v1/identify`).

Подключается, только если в Settings задан проект (host, access key, secret). Запрос —
свой httpx с подписью HMAC-SHA1 по документации ACRCloud, без их C-SDK. Форма ответа —
по документации: живого ключа на этапе 6-0 не было (ARCHITECTURE.md, 11j).
"""

import base64
import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Any, Final

import httpx

from syncplaylists.modules.recognition.application.ports import RecognizerThrottle
from syncplaylists.modules.recognition.domain.errors import (
    RecognizerBusyError,
    RecognizerUnavailableError,
)
from syncplaylists.shared_kernel.domain.search import RecognizedTrack
from syncplaylists.shared_kernel.domain.value_objects import ISRC

PROVIDER: Final = "acrcloud"
_PATH: Final = "/v1/identify"
_NO_RESULT: Final = 1001
# Лимиты проекта: 3003 — исчерпана квота (сутки/месяц), 3015 — запросов в секунду.
_QUOTA_EXCEEDED: Final = 3003
_QPS_EXCEEDED: Final = 3015
_QUOTA_PAUSE_SECONDS: Final = 3600.0
_QPS_PAUSE_SECONDS: Final = 5.0
# ACRCloud не сообщает сдвиг скорости/тона, только score 0..100. «Та же запись» —
# только при почти полном совпадении; иначе результат — подсказка.
EXACT_MIN_SCORE: Final = 90


class AcrCloudRecognizer:
    provider = PROVIDER

    def __init__(
        self,
        http: httpx.AsyncClient,
        throttle: RecognizerThrottle,
        *,
        host: str,
        access_key: str,
        access_secret: str,
        timeout_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._http = http
        self._throttle = throttle
        self._url = f"https://{host}{_PATH}"
        self._access_key = access_key
        self._access_secret = access_secret.encode()
        self._timeout = timeout_seconds
        self._clock = clock

    async def recognize(self, wav: bytes) -> RecognizedTrack | None:
        wait = await self._throttle.acquire(PROVIDER)
        if wait is not None:
            raise RecognizerBusyError(PROVIDER, wait)
        timestamp = str(int(self._clock()))
        fields = {
            "access_key": self._access_key,
            "sample_bytes": str(len(wav)),
            "timestamp": timestamp,
            "signature": self.signature(timestamp),
            "data_type": "audio",
            "signature_version": "1",
        }
        try:
            response = await self._http.post(
                self._url,
                data=fields,
                files={"sample": ("sample.wav", wav, "audio/wav")},
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise RecognizerUnavailableError(f"{PROVIDER}: таймаут") from exc
        except httpx.HTTPError as exc:
            raise RecognizerUnavailableError(f"{PROVIDER}: {type(exc).__name__}") from exc
        if response.status_code == 429:
            await self._throttle.penalize(PROVIDER, _QPS_PAUSE_SECONDS)
            raise RecognizerBusyError(PROVIDER, _QPS_PAUSE_SECONDS)
        if not response.is_success:
            raise RecognizerUnavailableError(f"{PROVIDER}: HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise RecognizerUnavailableError(f"{PROVIDER}: ответ не JSON") from exc
        try:
            return parse_acrcloud(data)
        except RecognizerBusyError as exc:
            await self._throttle.penalize(PROVIDER, exc.retry_after_seconds)
            raise

    def signature(self, timestamp: str) -> str:
        string_to_sign = "\n".join(
            ["POST", _PATH, self._access_key, "audio", "1", timestamp]
        ).encode()
        digest = hmac.new(self._access_secret, string_to_sign, hashlib.sha1).digest()
        return base64.b64encode(digest).decode()


def parse_acrcloud(data: Any) -> RecognizedTrack | None:
    status = data.get("status") if isinstance(data, dict) else None
    code = status.get("code") if isinstance(status, dict) else None
    if code == _NO_RESULT:
        return None
    if code == _QUOTA_EXCEEDED:
        raise RecognizerBusyError(PROVIDER, _QUOTA_PAUSE_SECONDS)
    if code == _QPS_EXCEEDED:
        raise RecognizerBusyError(PROVIDER, _QPS_PAUSE_SECONDS)
    if code != 0:
        message = status.get("msg") if isinstance(status, dict) else None
        raise RecognizerUnavailableError(f"{PROVIDER}: код {code} {message or ''}".rstrip())
    metadata = data.get("metadata")
    music = metadata.get("music") if isinstance(metadata, dict) else None
    best = music[0] if isinstance(music, list) and music else None
    if not isinstance(best, dict):
        return None
    title = best.get("title")
    artists = [
        a["name"]
        for a in best.get("artists") or []
        if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"]
    ]
    if not isinstance(title, str) or not title or not artists:
        return None
    external = best.get("external_ids")
    score = best.get("score")
    offset = best.get("play_offset_ms")
    acrid = best.get("acrid")
    return RecognizedTrack(
        title=title,
        artist=", ".join(artists),
        provider=PROVIDER,
        isrc=_isrc(external.get("isrc") if isinstance(external, dict) else None),
        provider_ref=acrid if isinstance(acrid, str) else None,
        exact=isinstance(score, int | float) and score >= EXACT_MIN_SCORE,
        offset_ms=int(offset) if isinstance(offset, int | float) else None,
    )


def _isrc(value: Any) -> ISRC | None:
    # Бывает и строкой, и списком строк.
    candidates = value if isinstance(value, list) else [value]
    for candidate in candidates:
        if isinstance(candidate, str):
            try:
                return ISRC(candidate.strip().upper())
            except ValueError:
                continue
    return None
