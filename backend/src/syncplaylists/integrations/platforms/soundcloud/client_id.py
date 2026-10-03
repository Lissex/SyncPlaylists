"""client_id веб-клиента SoundCloud для api-v2.

Внутренний API сайта требует client_id приложения-сайта. Он лежит в одном из JS-бандлов
главной страницы (`client_id:"<32 символа>"`, проверено 2026-10-03 — в последнем) и время
от времени меняется — тогда v2 отвечает 401 на всё подряд. Достаём его со страницы,
кэшируем в процессе и в Redis (общий на все воркеры) и обновляем, когда транспорт
сообщает, что он не принят.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Final, Protocol
from urllib.parse import urlsplit

import httpx

from syncplaylists.shared_kernel.domain.errors import PlatformUnavailableError
from syncplaylists.shared_kernel.domain.value_objects import Platform

logger = logging.getLogger(__name__)

_PLATFORM: Final = Platform.SOUNDCLOUD
SITE_URL: Final = "https://soundcloud.com/"
CACHE_KEY: Final = "soundcloud:client_id"
# Скачиваем только с этих хостов: адреса скриптов берутся из HTML, и без allowlist
# подменённая страница могла бы отправить нас куда угодно (SSRF).
_ASSET_HOSTS: Final = frozenset({"a-v2.sndcdn.com"})
_SCRIPT_SRC: Final = re.compile(r'<script[^>]+src="(?P<src>https://[^"]+\.js)"')
_CLIENT_ID: Final = re.compile(r'client_id\s*[:=]\s*"(?P<id>[A-Za-z0-9]{32})"')
BROWSER_USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


class TextCache(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


def extract_script_urls(html: str) -> list[str]:
    """Адреса бандлов со страницы — только с разрешённых хостов, в порядке на странице."""
    urls = [m["src"] for m in _SCRIPT_SRC.finditer(html)]
    return [url for url in urls if urlsplit(url).hostname in _ASSET_HOSTS]


def extract_client_id(script: str) -> str | None:
    match = _CLIENT_ID.search(script)
    return match["id"] if match else None


class ClientIdProvider:
    def __init__(
        self,
        http: httpx.AsyncClient,
        cache: TextCache | None,
        *,
        ttl_seconds: int,
        min_refresh_seconds: float,
        timeout_seconds: float,
        override: str | None = None,
        on_request: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._cache = cache
        self._ttl = ttl_seconds
        self._min_refresh = min_refresh_seconds
        self._timeout = timeout_seconds
        self._override = override
        # Учёт запросов к сайту (server-счётчик лимитера): это тоже трафик с нашего IP.
        self._on_request = on_request
        self._clock = clock
        self._value: str | None = override
        self._scraped_at: float | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        if self._value is not None:
            return self._value
        async with self._lock:
            if self._value is None:
                self._value = await self._read_cache() or await self._scrape()
            return self._value

    async def refresh(self, stale: str) -> str | None:
        """client_id `stale` не приняли. Возвращает другой client_id, если удалось его
        получить, иначе None — тогда дело не в client_id (его только что обновляли или
        сайт отдаёт тот же), и 401 относится к токену пользователя."""
        if self._override is not None:
            return None
        async with self._lock:
            if self._value is not None and self._value != stale:
                return self._value  # уже обновил параллельный запрос этого процесса
            cached = await self._read_cache()
            if cached is not None and cached != stale:
                self._value = cached  # обновил другой воркер — сайт не качаем
                return cached
            if self._scraped_at is not None and (
                self._clock() - self._scraped_at < self._min_refresh
            ):
                return None
            fresh = await self._scrape()
            self._value = fresh
            return fresh if fresh != stale else None

    async def _read_cache(self) -> str | None:
        if self._cache is None:
            return None
        try:
            return await self._cache.get(CACHE_KEY)
        except Exception as exc:  # кэш — оптимизация, его сбой не ломает запросы
            logger.warning("Кэш client_id SoundCloud недоступен (%s)", type(exc).__name__)
            return None

    async def _scrape(self) -> str:
        self._scraped_at = self._clock()
        html = await self._fetch(SITE_URL)
        # client_id обычно в последнем из бандлов — идём с конца.
        for url in reversed(extract_script_urls(html)):
            client_id = extract_client_id(await self._fetch(url))
            if client_id is None:
                continue
            logger.info("SoundCloud: получен client_id со страницы сайта")
            if self._cache is not None:
                try:
                    await self._cache.set(CACHE_KEY, client_id, self._ttl)
                except Exception as exc:
                    logger.warning("Кэш client_id не записан (%s)", type(exc).__name__)
            return client_id
        raise PlatformUnavailableError(_PLATFORM, "client_id не найден в скриптах сайта")

    async def _fetch(self, url: str) -> str:
        if self._on_request is not None:
            await self._on_request()
        try:
            response = await self._http.get(
                url, headers={"User-Agent": BROWSER_USER_AGENT}, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise PlatformUnavailableError(_PLATFORM, f"сайт: {type(exc).__name__}") from exc
        if not response.is_success:
            raise PlatformUnavailableError(_PLATFORM, f"сайт: HTTP {response.status_code}")
        return response.text
