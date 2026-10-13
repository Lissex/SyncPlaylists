"""Спайк 6-0: какое аудио реально отдают SoundCloud (анонимно) и Яндекс (по токену).

    cd backend
    uv run python -m tests.tools.audio_probe --soundcloud <ссылка на трек> ... \
        --yandex <id трека> ...

Только чтение, по одному запросу в секунду. Треки целиком не скачиваются:
- progressive — один Range-запрос на первые 1024 байта, чтобы узнать, есть ли Range
  (206) и какой размер файла;
- HLS — только сам m3u8: есть ли #EXT-X-KEY, длительность и число сегментов. Из
  незашифрованного потока — Range к первому сегменту. Ни сегменты, ни ключи
  зашифрованных потоков не запрашиваются (правило этапа 6: шифрованное —
  только NO_AUDIO).

Подписанные ссылки, track_authorization, s/ts из XML Яндекса и токены не печатаются.
В записанных ответах они заменены на «<redacted>», а у адресов оставлен только хост.
Пишется в tests/fixtures/{soundcloud,yandex}/recorded/audio/ (в .gitignore). Перед
переносом в фикстуры — просмотреть глазами.
Для Яндекса нужен YANDEX_LIVE_TOKEN в .env. SoundCloud ходит без токена, с client_id
веб-клиента — так же, как сервер читает публичное.
"""

import argparse
import asyncio
import json
import re
import xml.dom.minidom as minidom
from pathlib import Path
from typing import Any

import httpx
from yandex_music import ClientAsync

from syncplaylists.integrations.platforms.soundcloud.client_id import (
    BROWSER_USER_AGENT,
    ClientIdProvider,
)
from syncplaylists.integrations.platforms.soundcloud.transport import (
    V2_BASE_URL,
    SoundCloudTransport,
)
from syncplaylists.integrations.platforms.yandex.transport import HttpxYandexRequest
from tests.live.conftest import LiveSettings

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_SC_OUT = _FIXTURES / "soundcloud" / "recorded" / "audio"
_YA_OUT = _FIXTURES / "yandex" / "recorded" / "audio"
_PAUSE_SECONDS = 1.0
_SECRET_KEYS = frozenset({"track_authorization", "secret_token", "secret_uri", "s", "ts"})
_URL = re.compile(r"https?://[^\s\"'<>]+")


def _host_only(text: str) -> str:
    return _URL.sub(lambda m: f"https://{httpx.URL(m.group(0)).host}/<redacted>", text)


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if key in _SECRET_KEYS else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        return _host_only(value)
    return value


def _save(directory: Path, name: str, data: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_text(json.dumps(_scrub(data), ensure_ascii=False, indent=2), encoding="utf-8")


async def _range_probe(
    http: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None
) -> str:
    await asyncio.sleep(_PAUSE_SECONDS)
    response = await http.get(
        url,
        headers={"Range": "bytes=0-1023", "User-Agent": BROWSER_USER_AGENT, **(headers or {})},
        timeout=20,
    )
    if response.is_redirect:
        target = httpx.URL(response.headers["location"])
        if target.host.endswith((".yandex.net", ".yandex.ru", ".sndcdn.com")):
            # Только на хосты площадки; заголовок авторизации на другой хост не несём.
            same_host = target.host == httpx.URL(url).host
            onward = await _range_probe(http, str(target), headers if same_host else None)
            return f"{response.status_code} → хост {target.host}: {onward}"
        return f"{response.status_code} → чужой хост {target.host}, не идём"
    total = response.headers.get("content-range", "").rpartition("/")[2] or "?"
    return (
        f"Range → {response.status_code}, {response.headers.get('content-type')}, "
        f"получено {len(response.content)} Б, файл {total} Б, "
        f"accept-ranges={response.headers.get('accept-ranges')}"
    )


def _describe_m3u8(text: str) -> tuple[str, list[str], bool]:
    keys = [line for line in text.splitlines() if line.startswith("#EXT-X-KEY")]
    methods = sorted({m.group(1) for k in keys if (m := re.search(r"METHOD=([A-Z0-9-]+)", k))})
    encrypted = any(method != "NONE" for method in methods)
    durations = [float(x) for x in re.findall(r"#EXTINF:([\d.]+)", text)]
    segments = [line for line in text.splitlines() if line and not line.startswith("#")]
    hosts = sorted({httpx.URL(s).host for s in segments if s.startswith("http")})
    summary = (
        f"сегментов {len(segments)}, всего {sum(durations):.1f} с, "
        f"сегмент ~{(sum(durations) / len(durations)) if durations else 0:.1f} с, "
        f"EXT-X-KEY: {methods or 'нет'}, хосты сегментов: {hosts or 'относительные'}"
    )
    return summary, segments, encrypted


async def probe_soundcloud(
    http: httpx.AsyncClient, urls: list[str], search: str | None, search_limit: int
) -> None:
    client_ids = ClientIdProvider(
        http, None, ttl_seconds=3600, min_refresh_seconds=60, timeout_seconds=20
    )
    api = SoundCloudTransport(http, base_url=V2_BASE_URL, timeout_seconds=20, client_ids=client_ids)
    client_id = await client_ids.get()
    tracks: list[dict[str, Any]] = []
    for url in urls:
        await asyncio.sleep(_PAUSE_SECONDS)
        tracks.append(await api.get("/resolve", {"url": url}))
    if search:
        await asyncio.sleep(_PAUSE_SECONDS)
        found = await api.get("/search/tracks", {"q": search, "limit": search_limit})
        tracks.extend(found.get("collection") or [])
    for index, track in enumerate(tracks):
        _save(_SC_OUT, f"track_{index}.json", track)
        print(f"\n=== SoundCloud #{index}: {track.get('title')!r}")
        print(
            f"policy={track.get('policy')} duration={track.get('duration')} "
            f"full_duration={track.get('full_duration')} streamable={track.get('streamable')} "
            f"has track_authorization={bool(track.get('track_authorization'))}"
        )
        transcodings = (track.get("media") or {}).get("transcodings") or []
        if not transcodings:
            print("media.transcodings пусто")
        for t_index, transcoding in enumerate(transcodings):
            fmt = transcoding.get("format") or {}
            protocol = str(fmt.get("protocol"))
            print(
                f"  [{t_index}] protocol={protocol} mime={fmt.get('mime_type')} "
                f"preset={transcoding.get('preset')} quality={transcoding.get('quality')} "
                f"snipped={transcoding.get('snipped')} duration={transcoding.get('duration')}"
            )
            if "encrypted" in protocol or protocol in {"ctr-encrypted-hls", "cbc-encrypted-hls"}:
                print("      зашифровано — не запрашиваем (NO_AUDIO)")
                continue
            await asyncio.sleep(_PAUSE_SECONDS)
            params = {"client_id": client_id}
            if track.get("track_authorization"):
                params["track_authorization"] = track["track_authorization"]
            stream = await http.get(
                transcoding["url"],
                params=params,
                headers={"User-Agent": BROWSER_USER_AGENT},
                timeout=20,
            )
            print(f"      transcoding.url → {stream.status_code}")
            if not stream.is_success:
                print(f"      тело: {_host_only(stream.text[:200])}")
                continue
            media_url = stream.json().get("url", "")
            print(f"      поток: хост {httpx.URL(media_url).host}")
            if protocol == "progressive":
                print(f"      {await _range_probe(http, media_url)}")
                continue
            await asyncio.sleep(_PAUSE_SECONDS)
            playlist = await http.get(
                media_url, headers={"User-Agent": BROWSER_USER_AGENT}, timeout=20
            )
            summary, segments, encrypted = _describe_m3u8(playlist.text)
            _save(_SC_OUT, f"track_{index}_{t_index}.m3u8", _host_only(playlist.text))
            print(f"      m3u8 → {playlist.status_code}: {summary}")
            if encrypted:
                print("      зашифровано — сегменты не запрашиваем (NO_AUDIO)")
            elif segments and segments[0].startswith("http"):
                print(f"      первый сегмент: {await _range_probe(http, segments[0])}")


async def probe_yandex(http: httpx.AsyncClient, track_ids: list[str], token: str) -> None:
    client = ClientAsync(token, request=HttpxYandexRequest(http, timeout_seconds=20))
    for track_id in track_ids:
        await asyncio.sleep(_PAUSE_SECONDS)
        infos = await client.tracks_download_info(track_id)
        _save(
            _YA_OUT,
            f"download_info_{track_id}.json",
            [
                {
                    "codec": i.codec,
                    "bitrate_in_kbps": i.bitrate_in_kbps,
                    "gain": i.gain,
                    "preview": i.preview,
                    "direct": i.direct,
                    "download_info_url": i.download_info_url,
                }
                for i in infos
            ],
        )
        print(f"\n=== Яндекс {track_id}: вариантов {len(infos)}")
        for info in infos:
            print(
                f"  codec={info.codec} bitrate={info.bitrate_in_kbps} preview={info.preview} "
                f"direct={info.direct} хост xml={httpx.URL(info.download_info_url).host}"
            )
        mp3 = sorted((i for i in infos if i.codec == "mp3"), key=lambda i: i.bitrate_in_kbps)
        if not mp3:
            print("  mp3 нет — ссылку не строим")
            continue
        chosen = mp3[0]
        # XML со ссылкой отдаётся только с авторизацией: без неё Яндекс рвёт соединение
        # (RemoteProtocolError). retrieve() библиотеки заголовок не ставит.
        auth = {"Authorization": f"OAuth {token}", "User-Agent": BROWSER_USER_AGENT}
        await asyncio.sleep(_PAUSE_SECONDS)
        response = await http.get(chosen.download_info_url, headers=auth, timeout=20)
        print(f"  xml → {response.status_code} {response.headers.get('content-type')}")
        if not response.is_success:
            continue
        xml = response.content
        doc = minidom.parseString(xml)  # ответ Яндекса, не пользовательский ввод
        _save(
            _YA_OUT,
            f"download_info_{track_id}.xml",
            re.sub(r"<(s|ts|path)>[^<]*</\1>", r"<\1><redacted></\1>", xml.decode("utf-8")),
        )
        hosts = [n.firstChild.nodeValue for n in doc.getElementsByTagName("host") if n.firstChild]
        print(f"  хост файла {hosts}, выбран {chosen.codec} {chosen.bitrate_in_kbps}")
        # Подпись ссылки — та же, что строит DownloadInfo.get_direct_link библиотеки.
        link = chosen._DownloadInfo__build_direct_link(xml)
        print(f"  без авторизации: {await _range_probe(http, link)}")
        print(f"  с авторизацией:  {await _range_probe(http, link, auth)}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--soundcloud", nargs="*", default=[], metavar="URL")
    parser.add_argument("--soundcloud-search", metavar="QUERY")
    parser.add_argument("--search-limit", type=int, default=3)
    parser.add_argument("--yandex", nargs="*", default=[], metavar="TRACK_ID")
    args = parser.parse_args()
    async with httpx.AsyncClient(follow_redirects=False) as http:
        if args.soundcloud or args.soundcloud_search:
            await probe_soundcloud(http, args.soundcloud, args.soundcloud_search, args.search_limit)
        if args.yandex:
            token = LiveSettings().yandex_live_token
            if token is None:
                raise SystemExit("YANDEX_LIVE_TOKEN не задан в .env")
            await probe_yandex(http, args.yandex, token.get_secret_value())


if __name__ == "__main__":
    asyncio.run(main())
