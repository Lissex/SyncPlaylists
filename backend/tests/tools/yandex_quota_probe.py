"""Замер: на что считается квота поиска Яндекс Музыки — на токен или на IP.

    cd backend
    uv run python -m tests.tools.yandex_quota_probe [--max 30]

Нужны YANDEX_LIVE_TOKEN (аккаунт A) и YANDEX_LIVE_TOKEN_2 (аккаунт B) в .env. Делает
только поиски треков, ничего в аккаунтах не меняет:
1. A — один поиск (до замера A уже упирался в 429);
2. B — поиски подряд, не больше --max, с паузой 1 с; остановка на первом 429;
3. A — ещё один поиск.
Печатает только статусы, счётчики и Retry-After — без токенов и ответов. Вывод:
- A = 429, B проходит → квота на токен;
- A = 429, B = 429 с первого запроса → квота на IP (или общий лимит сервера);
- A проходит → квота A уже восстановилась, замер неинформативен (повторить позже).

Второго аккаунта нет — режим одного токена и двух сетей:
    uv run python -m tests.tools.yandex_quota_probe --network home --max 150
    (переключиться на другую сеть, например раздачу с телефона)
    uv run python -m tests.tools.yandex_quota_probe --network other
"""

import argparse
import asyncio
import time
from datetime import datetime
from typing import Final

import httpx

from tests.live.conftest import LiveSettings

_API: Final = "https://api.music.yandex.net"
_QUERIES: Final = (
    "Кино Группа крови",
    "Queen Bohemian Rhapsody",
    "Nirvana Smells Like Teen Spirit",
    "Michael Jackson Billie Jean",
    "Eagles Hotel California",
    "Oasis Wonderwall",
    "Земфира Хочешь",
    "Сплин Выхода нет",
    "Daft Punk Get Lucky",
    "The Weeknd Blinding Lights",
)


async def _search(http: httpx.AsyncClient, token: str, n: int) -> tuple[int, str]:
    response = await http.get(
        "/search",
        params={
            "text": _QUERIES[n % len(_QUERIES)],
            "type": "track",
            "page": 0,
            "nocorrect": "False",
        },
        headers={
            "Authorization": f"OAuth {token}",
            "X-Yandex-Music-Client": "YandexMusicAndroid/24023621",
        },
    )
    return response.status_code, response.headers.get("retry-after", "-")


def _line(label: str, n: int, status: int, retry_after: str) -> None:
    print(f"{datetime.now():%H:%M:%S}  {label} #{n:<2}  HTTP {status}  Retry-After {retry_after}")


async def main(max_b: int) -> None:
    settings = LiveSettings()
    if settings.yandex_live_token is None or settings.yandex_live_token_2 is None:
        raise SystemExit("Нужны YANDEX_LIVE_TOKEN и YANDEX_LIVE_TOKEN_2 в .env")
    token_a = settings.yandex_live_token.get_secret_value()
    token_b = settings.yandex_live_token_2.get_secret_value()
    if token_a == token_b:
        raise SystemExit("YANDEX_LIVE_TOKEN_2 совпадает с YANDEX_LIVE_TOKEN — нужен другой аккаунт")

    async with httpx.AsyncClient(base_url=_API, timeout=20) as http:
        a_before, retry = await _search(http, token_a, 0)
        _line("A", 1, a_before, retry)

        b_ok = 0
        b_first_429: int | None = None
        b_retry = "-"
        started = time.monotonic()
        for n in range(1, max_b + 1):
            status, retry = await _search(http, token_b, n)
            _line("B", n, status, retry)
            if status == 429:
                b_first_429, b_retry = n, retry
                break
            if status == 200:
                b_ok += 1
            await asyncio.sleep(1)
        elapsed = time.monotonic() - started

        a_after, retry = await _search(http, token_a, 1)
        _line("A", 2, a_after, retry)

    print("\nИтог:")
    print(f"  A до: HTTP {a_before}; A после: HTTP {a_after}")
    if b_first_429 is None:
        print(f"  B: {b_ok} успешных поисков из {max_b} за {elapsed:.0f} с, 429 не было")
    else:
        print(f"  B: {b_ok} успешных, 429 на запросе #{b_first_429} (Retry-After {b_retry})")
    if a_before != 429:
        print("  → A уже не на паузе — замер неинформативен, повторить, когда A снова получит 429.")
    elif b_first_429 == 1:
        print("  → Квота на IP (или общий лимит с этого адреса): B упёрся с первого запроса.")
    elif b_ok > 0:
        print("  → Квота на ТОКЕН: A на паузе, B с того же IP ищет.")
    else:
        print("  → Неоднозначно (у B ошибки не 429) — смотрите строки выше.")


async def single(max_searches: int, network: str, interval: float, parallel: int) -> None:
    """Один токен, две сети. Шаг `home`: поиски токеном A до первого 429 (квота
    исчерпана). Шаг `other` — тот же токен из ДРУГОЙ сети (раздача с телефона): прошёл →
    квота на IP, снова 429 → квота на токен."""
    settings = LiveSettings()
    if settings.yandex_live_token is None:
        raise SystemExit("Нужен YANDEX_LIVE_TOKEN в .env")
    token = settings.yandex_live_token.get_secret_value()
    limit = max_searches if network == "home" else 3
    ok = 0
    first_429: int | None = None
    retry_429 = "-"
    async with httpx.AsyncClient(base_url=_API, timeout=20) as http:
        n = 0
        while n < limit and first_429 is None:
            # Пачка из `parallel` одновременных поисков — как воркер с несколькими
            # run_match разом; parallel=1 — строго по одному.
            batch = list(range(n + 1, min(n + parallel, limit) + 1))
            results = await asyncio.gather(*(_search(http, token, i) for i in batch))
            for i, (status, retry) in zip(batch, results, strict=True):
                _line(f"A/{network}", i, status, retry)
                if status == 429 and first_429 is None:
                    first_429, retry_429 = i, retry
                elif status == 200:
                    ok += 1
            n = batch[-1]
            await asyncio.sleep(interval)

    print("\nИтог:")
    if network == "home":
        if first_429 is None:
            print(f"  {ok} успешных поисков, 429 не было — квота ещё не исчерпана;")
            print("  запустите шаг home ещё раз (или с большим --max).")
        else:
            print(f"  429 после {ok} успешных поисков (Retry-After {retry_429}).")
            print("  Теперь переключите компьютер на другую сеть (раздача с телефона) и сразу:")
            print("    uv run python -m tests.tools.yandex_quota_probe --network other")
    elif first_429 is None:
        print(f"  Из другой сети тот же токен ищет ({ok}/{limit}) → квота на IP.")
    elif ok == 0:
        print("  Из другой сети тот же токен сразу получил 429 → квота на ТОКЕН.")
    else:
        print("  Из другой сети часть поисков прошла, потом 429 — неоднозначно, смотрите строки.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=30, help="не больше N поисков")
    parser.add_argument(
        "--interval", type=float, default=1.0, help="пауза между поисками, с (один токен)"
    )
    parser.add_argument(
        "--parallel", type=int, default=1, help="одновременных поисков в пачке (один токен)"
    )
    parser.add_argument(
        "--network",
        choices=("home", "other"),
        help="режим одного токена: home — исчерпать квоту, other — проверить из другой сети",
    )
    args = parser.parse_args()
    if args.network:
        asyncio.run(single(args.max, args.network, args.interval, args.parallel))
    else:
        asyncio.run(main(args.max))
