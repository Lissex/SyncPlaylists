"""Спайк 6-0: распознаётся ли Shazam'ом окно 18 с из середины трека SoundCloud.

Только в контейнере (ffmpeg), каталог tests смонтирован, чтобы ответы легли в репозиторий:

    docker compose run --rm -v "${PWD}\\backend\\tests:/app/tests" api `
        python -m tests.tools.shazam_probe <ссылка на трек SoundCloud> ...

Что делает для каждой ссылки:
1. Читает трек анонимно (client_id веб-клиента), как сервер читает публичное.
2. Берёт незашифрованный транскодинг: progressive mp3, иначе HLS mp3, иначе HLS aac.
   Шифрованные (encrypted-hls, EXT-X-KEY с METHOD≠NONE) не трогает — правило этапа 6.
3. Качает только окно: Range у progressive, сегменты окна у HLS. Только хосты CDN
   SoundCloud, не больше 3 МБ.
4. ffmpeg → wav 16 кГц mono, 18 с из середины.
5. Подпись — shazamio-core. Запрос к Shazam — один, httpx, постоянные заголовки,
   без повторов на 429. Между треками — пауза 5 с.
6. Всё во временном каталоге, он удаляется в finally. Скрипт проверяет, что каталога нет.

Ответы Shazam пишутся в tests/fixtures/recognition/recorded/ (в .gitignore).
"""

import asyncio
import json
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from shazamio_core import Recognizer, SearchParams

from syncplaylists.integrations.platforms.soundcloud.client_id import (
    BROWSER_USER_AGENT,
    ClientIdProvider,
)
from syncplaylists.integrations.platforms.soundcloud.transport import (
    V2_BASE_URL,
    SoundCloudTransport,
)

_OUT = Path(__file__).resolve().parents[1] / "fixtures" / "recognition" / "recorded"
_WINDOW_SECONDS = 18
_SIGNATURE_SECONDS = 12
_MAX_BYTES = 3_000_000
_SHAZAM_PAUSE_SECONDS = 5.0
_CDN_HOSTS = (".sndcdn.com", ".soundcloud.cloud")
_SHAZAM_URL = (
    "https://amp.shazam.com/discovery/v5/en-US/GB/iphone/-/tag/{u1}/{u2}"
    "?sync=true&webv3=true&sampling=true&connected=&shazamapiversion=v3"
    "&sharehub=true&hubv5minorversion=v5.1&hidelb=true&video=v3"
)
_SHAZAM_HEADERS = {
    "X-Shazam-Platform": "IPHONE",
    "X-Shazam-AppVersion": "14.1.0",
    "Accept": "*/*",
    "Accept-Language": "en-US",
    "Content-Type": "application/json",
}


class NoAudioError(Exception):
    pass


def _check_host(url: str) -> str:
    host = httpx.URL(url).host
    if not host.endswith(_CDN_HOSTS):
        raise NoAudioError(f"хост {host} не из allowlist")
    return url


async def _get(http: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    await asyncio.sleep(1.0)
    response = await http.get(
        _check_host(url), headers={"User-Agent": BROWSER_USER_AGENT, **kwargs}, timeout=20
    )
    response.raise_for_status()
    return response


def _ranked(transcodings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def rank(t: dict[str, Any]) -> int | None:
        fmt = t.get("format") or {}
        protocol, mime = str(fmt.get("protocol")), str(fmt.get("mime_type"))
        if "encrypted" in protocol:
            return None
        if protocol == "progressive":
            return 0
        # audio/mpegurl — адаптивный плейлист (abr), анонимно отвечает 404: не mp3.
        if protocol == "hls" and mime == "audio/mpeg":
            return 1
        if protocol == "hls" and mime.startswith("audio/mp4"):
            return 2
        return None

    ranked = sorted((r, i) for i, t in enumerate(transcodings) if (r := rank(t)) is not None)
    if not ranked:
        raise NoAudioError("нет незашифрованных транскодингов")
    return [transcodings[i] for _, i in ranked]


async def _fetch_window(
    http: httpx.AsyncClient, media_url: str, protocol: str, start: float, duration: float
) -> tuple[bytes, float]:
    """Байты, покрывающие окно, и смещение начала окна внутри них (с)."""
    if protocol == "progressive":
        head = await _get(http, media_url, Range="bytes=0-0")
        total = int(head.headers["content-range"].rpartition("/")[2])
        first = max(0, int(total * (start - 2) / duration))
        last = min(total - 1, int(total * (start + _WINDOW_SECONDS + 2) / duration))
        if last - first > _MAX_BYTES:
            raise NoAudioError("окно больше лимита")
        chunk = await _get(http, media_url, Range=f"bytes={first}-{last}")
        return chunk.content, start - duration * first / total

    playlist = (await _get(http, media_url)).text
    if any(
        line.startswith("#EXT-X-KEY") and "METHOD=NONE" not in line
        for line in playlist.splitlines()
    ):
        raise NoAudioError("HLS зашифрован (EXT-X-KEY) — не расшифровываем")
    init = re.search(r'#EXT-X-MAP:URI="([^"]+)"', playlist)
    segments: list[tuple[float, str]] = []
    position = 0.0
    lines = playlist.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("#EXTINF:"):
            length = float(line[8:].split(",")[0])
            uri = lines[index + 1]
            if position + length > start and position < start + _WINDOW_SECONDS:
                segments.append((position, str(httpx.URL(media_url).join(uri))))
            position += length
    if not segments:
        raise NoAudioError("сегменты окна не найдены")
    parts = (
        [(await _get(http, str(httpx.URL(media_url).join(init.group(1))))).content] if init else []
    )
    for _, url in segments:
        parts.append((await _get(http, url)).content)
        if sum(map(len, parts)) > _MAX_BYTES:
            raise NoAudioError("окно больше лимита")
    return b"".join(parts), start - segments[0][0]


async def _to_wav(workdir: Path, data: bytes, offset: float) -> bytes:
    source, target = workdir / "in.bin", workdir / "out.wav"
    source.write_bytes(data)
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(offset, 0):.2f}", "-i", str(source), "-t", str(_WINDOW_SECONDS),
        "-ac", "1", "-ar", "16000", "-f", "wav", str(target),
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    _, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    if process.returncode != 0:
        raise NoAudioError(f"ffmpeg: {stderr.decode(errors='replace')[:200]}")
    return target.read_bytes()


async def _shazam(http: httpx.AsyncClient, wav: bytes) -> tuple[int, Any]:
    recognizer = Recognizer(segment_duration_seconds=_SIGNATURE_SECONDS)
    signature = await recognizer.recognize_bytes(
        wav, SearchParams(segment_duration_seconds=_SIGNATURE_SECONDS)
    )
    body = {
        "timezone": "Europe/Moscow",
        "signature": {"uri": signature.signature.uri, "samplems": signature.signature.samples},
        "timestamp": signature.timestamp,
        "context": {},
        "geolocation": {},
    }
    url = _SHAZAM_URL.format(u1=str(uuid.uuid4()).upper(), u2=str(uuid.uuid4()).upper())
    response = await http.post(url, json=body, headers=_SHAZAM_HEADERS, timeout=20)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text[:300]


def _summary(data: Any) -> str:
    if not isinstance(data, dict):
        return f"не JSON: {data!r}"
    track = data.get("track")
    if not track:
        return f"не узнан (matches={len(data.get('matches') or [])})"
    return (
        f"{track.get('subtitle')} — {track.get('title')} | isrc={track.get('isrc')} "
        f"key={track.get('key')} matches={len(data.get('matches') or [])}"
    )


async def main(urls: list[str]) -> None:
    async with httpx.AsyncClient(follow_redirects=False) as http:
        client_ids = ClientIdProvider(
            http, None, ttl_seconds=3600, min_refresh_seconds=60, timeout_seconds=20
        )
        api = SoundCloudTransport(
            http, base_url=V2_BASE_URL, timeout_seconds=20, client_ids=client_ids
        )
        client_id = await client_ids.get()
        for index, link in enumerate(urls):
            if index:
                await asyncio.sleep(_SHAZAM_PAUSE_SECONDS)
            track = await api.get("/resolve", {"url": link})
            print(
                f"\n=== #{index}: {track.get('user', {}).get('username')} | {track.get('title')!r}"
            )
            workdir_path: Path | None = None
            try:
                params = {"client_id": client_id}
                if track.get("track_authorization"):
                    params["track_authorization"] = track["track_authorization"]
                stream: httpx.Response | None = None
                for transcoding in _ranked((track.get("media") or {}).get("transcodings") or []):
                    fmt = transcoding["format"]
                    await asyncio.sleep(1.0)
                    stream = await http.get(transcoding["url"], params=params, timeout=20)
                    if stream.is_success:
                        break
                    print(f"{fmt['protocol']} {fmt['mime_type']} → {stream.status_code}, следующий")
                if stream is None or not stream.is_success:
                    raise NoAudioError("ни один транскодинг не ответил")
                duration = float(transcoding.get("duration") or track["duration"]) / 1000
                start = max(0.0, duration / 2 - _WINDOW_SECONDS / 2)
                print(f"транскодинг {fmt['protocol']} {fmt['mime_type']}, окно {start:.1f}–"
                      f"{start + _WINDOW_SECONDS:.1f} с из {duration:.1f}")  # fmt: skip
                with tempfile.TemporaryDirectory(prefix="recognize-") as workdir:
                    workdir_path = Path(workdir)
                    began = time.monotonic()
                    data, offset = await _fetch_window(
                        http, stream.json()["url"], fmt["protocol"], start, duration
                    )
                    wav = await _to_wav(workdir_path, data, offset)
                    print(f"скачано {len(data)} Б, wav {len(wav)} Б, "
                          f"{time.monotonic() - began:.1f} с")  # fmt: skip
                    status, answer = await _shazam(http, wav)
                print(f"Shazam → {status}: {_summary(answer)}")
                _OUT.mkdir(parents=True, exist_ok=True)
                (_OUT / f"shazam_{index}.json").write_text(
                    json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                if status == 429:
                    print("429 — останавливаемся, повторов не делаем")
                    break
            except NoAudioError as exc:
                print(f"NO_AUDIO: {exc}")
            except httpx.HTTPStatusError as exc:
                # Без URL: в нём client_id и track_authorization.
                print(f"HTTP {exc.response.status_code} от {exc.request.url.host}")
            finally:
                if workdir_path is not None:
                    print(f"временный каталог удалён: {not workdir_path.exists()}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1:]))
