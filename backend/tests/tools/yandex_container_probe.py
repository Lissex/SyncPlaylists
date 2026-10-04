"""Тот же простой поиск, что у yandex_quota_probe, но без зависимостей проекта — чтобы
запустить его ВНУТРИ контейнера воркера и сравнить с хостом (этап 4b-4).

    docker compose cp .env worker:/tmp/probe.env
    Get-Content backend/tests/tools/yandex_container_probe.py |
        docker compose exec -T worker python -
    docker compose exec worker rm /tmp/probe.env

Печатает только версии Python/OpenSSL и статусы ответов — без токена.
"""

import ssl
import sys
import time
from pathlib import Path

import httpx

_ENV = Path("/tmp/probe.env")
_QUERIES = (
    "august & fendiglock сам не свой",
    "live love asap peso",
    "fakemink mink",
    "ilya konoplev ноктюрн",
    "saluki sport",
    "Кино Группа крови",
    "Queen Bohemian Rhapsody",
    "Земфира Хочешь",
    "Сплин Выхода нет",
    "Daft Punk Get Lucky",
)


def _token() -> str:
    for line in _ENV.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("YANDEX_LIVE_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("YANDEX_LIVE_TOKEN нет в /tmp/probe.env")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(f"python {sys.version.split()[0]}, {ssl.OPENSSL_VERSION}, httpx {httpx.__version__}")
    token = _token()
    with httpx.Client(base_url="https://api.music.yandex.net", timeout=20) as http:
        for n, text in enumerate(_QUERIES, 1):
            response = http.get(
                "/search",
                params={"text": text, "type": "track", "page": 0, "nocorrect": "False"},
                headers={
                    "Authorization": f"OAuth {token}",
                    "X-Yandex-Music-Client": "YandexMusicAndroid/24023621",
                },
            )
            retry = response.headers.get("retry-after", "-")
            stamp = time.strftime("%H:%M:%S")
            print(f"{stamp}  контейнер #{n:<2} HTTP {response.status_code}  Retry-After {retry}")
            time.sleep(1)


main()
