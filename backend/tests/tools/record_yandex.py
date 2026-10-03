"""Записать свежие ответы API Яндекс Музыки для сверки с tests/fixtures/yandex.

    cd backend
    uv run python -m tests.tools.record_yandex

Нужен YANDEX_LIVE_TOKEN в .env. Ответы пишутся в tests/fixtures/yandex/recorded/ (в
.gitignore) уже вычищенными: uid, login, имена, e-mail, телефоны заменены фиктивными.
Это материал для сверки формы ответов с фикстурами (поля могли поменяться), а не
готовые фикстуры: тесты опираются на конкретное содержимое фикстур. Перед тем как
переносить что-то из recorded/ в фикстуры — просмотреть глазами.
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx

from tests.live.conftest import LiveSettings

_API = "https://api.music.yandex.net"
_OUT = Path(__file__).resolve().parents[1] / "fixtures" / "yandex" / "recorded"
_FAKE_UID = 123456789
_FAKE_LOGIN = "test.user"
_PERSONAL_KEYS = frozenset(
    {
        "birthday",
        "defaultEmail",
        "passport-phones",
        "passportPhones",
        "email",
        "phone",
        "userhash",
    }
)


def _scrub(value: Any, uid: int, login: str) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in _PERSONAL_KEYS:
                continue
            if key in {"uid", "owner_uid"} and item == uid:
                result[key] = _FAKE_UID
            elif key == "login" and item == login:
                result[key] = _FAKE_LOGIN
            elif key in {"fullName", "firstName", "secondName", "displayName"}:
                result[key] = "Test User"
            else:
                result[key] = _scrub(item, uid, login)
        return result
    if isinstance(value, list):
        return [_scrub(item, uid, login) for item in value]
    if isinstance(value, str):
        value = value.replace(str(uid), str(_FAKE_UID))
        return re.sub(re.escape(login), _FAKE_LOGIN, value) if login else value
    return value


async def main() -> None:
    settings = LiveSettings()
    if settings.yandex_live_token is None:
        raise SystemExit("YANDEX_LIVE_TOKEN не задан в .env")
    headers = {
        "Authorization": f"OAuth {settings.yandex_live_token.get_secret_value()}",
        "X-Yandex-Music-Client": "YandexMusicAndroid/24023621",
    }
    async with httpx.AsyncClient(base_url=_API, headers=headers, timeout=20) as http:
        status = (await http.get("/account/status")).json()
        account = status["result"]["account"]
        uid, login = int(account["uid"]), str(account.get("login") or "")

        likes = (await http.get(f"/users/{uid}/likes/tracks")).json()
        likes["result"]["library"]["tracks"] = likes["result"]["library"]["tracks"][:5]
        ids = [f"{t['id']}:{t['albumId']}" for t in likes["result"]["library"]["tracks"]]
        tracks = (await http.post("/tracks", data={"track-ids": ",".join(ids)})).json()
        # Те же параметры, что шлёт yandex-music: без page API отвечает 400 validate.
        search = (
            await http.get(
                "/search",
                params={
                    "text": "Кино Группа крови",
                    "type": "track",
                    "page": 0,
                    "nocorrect": "False",
                },
            )
        ).json()
        playlists = (await http.get(f"/users/{uid}/playlists/list")).json()

        responses: dict[str, Any] = {
            "account_status": status,
            "likes_tracks": likes,
            "tracks": tracks,
            "search_tracks": search,
            "playlists_list": playlists,
        }
        if playlists["result"]:
            kind = playlists["result"][0]["kind"]
            responses["playlist"] = (await http.get(f"/users/{uid}/playlists/{kind}")).json()

    _OUT.mkdir(parents=True, exist_ok=True)
    for name, data in responses.items():
        path = _OUT / f"{name}.json"
        path.write_text(
            json.dumps(_scrub(data, uid, login), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"записано: {path}")


if __name__ == "__main__":
    asyncio.run(main())
