"""Записать свежие ответы api-v2 SoundCloud для сверки с tests/fixtures/soundcloud и
выгрузить реальные названия для проверки нормализатора.

    cd backend
    uv run python -m tests.tools.record_soundcloud [ссылка на сет ...]

Нужен SOUNDCLOUD_LIVE_TOKEN в .env. Только чтение: профиль, первые страницы лайков,
свои сеты, поиск, догрузка треков, плюс сеты по ссылкам из аргументов (в т.ч. приватные
s-…). Всё пишется в tests/fixtures/soundcloud/recorded/ (в .gitignore) уже вычищенным:
id/имя/ссылки вашего аккаунта, e-mail, город, аватар и токены заменены фиктивными.

titles.csv там же — «название | заливщик | publisher_metadata.artist | как разобрал
TrackNormalizer»: материал, чтобы найти мусорные теги и неразобранные «Artist - Title».
Перед переносом чего-либо в фикстуры или корпус — просмотреть глазами.
"""

import asyncio
import csv
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from syncplaylists.integrations.platforms.soundcloud.client_id import ClientIdProvider
from syncplaylists.integrations.platforms.soundcloud.transport import (
    V2_BASE_URL,
    SoundCloudTransport,
)
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from tests.live.conftest import LiveSettings

_OUT = Path(__file__).resolve().parents[1] / "fixtures" / "soundcloud" / "recorded"
_FAKE_ID = 900001
_FAKE_PERMALINK = "test-listener"
_PERSONAL_KEYS = frozenset(
    {
        "email",
        "primary_email_confirmed",
        "city",
        "country_code",
        "date_of_birth",
        "first_name",
        "last_name",
        "full_name",
        "description",
        "phone",
        "secret_token",
        "secret_uri",
        "track_authorization",
        "avatar_url",
        "visuals",
        "creator_subscriptions",
        "creator_subscription",
        "quota",
        "private_playlists_count",
        "private_tracks_count",
    }
)


def _scrub(value: Any, own_id: int, permalink: str, username: str) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in _PERSONAL_KEYS:
                result[key] = None
            elif item == own_id:
                result[key] = _FAKE_ID
            else:
                result[key] = _scrub(item, own_id, permalink, username)
        return result
    if isinstance(value, list):
        return [_scrub(item, own_id, permalink, username) for item in value]
    if isinstance(value, str):
        value = value.replace(str(own_id), str(_FAKE_ID))
        if permalink:
            value = value.replace(permalink, _FAKE_PERMALINK)
        if username and value == username:
            value = "Test Listener"
        return value
    return value


def _tracks_of(body: Any) -> list[dict[str, Any]]:
    items = body.get("collection", []) if isinstance(body, dict) else body
    tracks = []
    for item in items or []:
        track = item.get("track", item) if isinstance(item, dict) else None
        if isinstance(track, dict) and track.get("title"):
            tracks.append(track)
    return tracks


async def main(urls: list[str]) -> None:
    settings = LiveSettings()
    if settings.soundcloud_live_token is None:
        raise SystemExit("SOUNDCLOUD_LIVE_TOKEN не задан в .env")
    async with httpx.AsyncClient() as http:
        client_ids = ClientIdProvider(
            http, None, ttl_seconds=3600, min_refresh_seconds=60, timeout_seconds=20
        )
        api = SoundCloudTransport(
            http,
            base_url=V2_BASE_URL,
            timeout_seconds=20,
            client_ids=client_ids,
            static_token=settings.soundcloud_live_token.get_secret_value(),
        )
        me = await api.get("/me")
        own_id, permalink = int(me["id"]), str(me.get("permalink") or "")
        username = str(me.get("username") or "")
        params = {"limit": 50, "linked_partitioning": 1}

        responses: dict[str, Any] = {"me": me}
        responses["likes"] = await api.get(f"/users/{own_id}/track_likes", params)
        responses["like_ids"] = await api.get("/me/track_likes/ids", {"limit": 50})
        responses["own_playlists"] = await api.get(
            f"/users/{own_id}/playlists_without_albums", {"limit": 5}
        )
        responses["search"] = await api.get(
            "/search/tracks", {"q": "juice wrld lucid dreams", "limit": 20}
        )
        for index, url in enumerate(urls):
            playlist = await api.get("/resolve", {"url": url})
            responses[f"playlist_{index}"] = playlist
            stubs = [t["id"] for t in playlist.get("tracks", []) if not t.get("title")][:50]
            if stubs:
                query: dict[str, Any] = {"ids": ",".join(map(str, stubs))}
                if playlist.get("secret_token"):
                    query |= {
                        "playlistId": playlist["id"],
                        "playlistSecretToken": playlist["secret_token"],
                    }
                responses[f"playlist_{index}_tracks"] = await api.get("/tracks", query)

    _OUT.mkdir(parents=True, exist_ok=True)
    titles: list[dict[str, Any]] = []
    for name, data in responses.items():
        path = _OUT / f"{name}.json"
        path.write_text(
            json.dumps(_scrub(data, own_id, permalink, username), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"записано: {path}")
        if name == "me":
            continue
        if isinstance(data, dict) and isinstance(data.get("tracks"), list):
            titles.extend(_tracks_of(data["tracks"]))  # сет: полные треки из шапки
        else:
            titles.extend(_tracks_of(data))

    normalizer = TrackNormalizer()
    path = _OUT / "titles.csv"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter="|")
        writer.writerow(["title", "uploader", "publisher_artist", "norm_title", "norm_artist"])
        seen: set[int] = set()
        for track in titles:
            if track["id"] in seen:
                continue
            seen.add(track["id"])
            uploader = (track.get("user") or {}).get("username") or ""
            publisher = (track.get("publisher_metadata") or {}).get("artist") or ""
            parsed = normalizer.normalize(track["title"], publisher or uploader)
            writer.writerow([track["title"], uploader, publisher, parsed.title, parsed.artist])
    print(f"названия: {path} ({len(seen)})")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
