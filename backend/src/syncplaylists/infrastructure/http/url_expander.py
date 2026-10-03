from typing import Final
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from syncplaylists.shared_kernel.domain.errors import UnsupportedLinkError

# Только эти хосты мы сами запрашиваем. Ссылка, которую ввёл пользователь, — чужие
# данные: ходить по произвольному URL с сервера — это SSRF (внутренняя сеть,
# метаданные облака). Поэтому запрос уходит только на известный сокращатель, а
# как только редирект указывает на любой другой хост, мы останавливаемся и
# отдаём URL парсерам, не запрашивая его.
SHORT_LINK_HOSTS: Final = frozenset(
    {
        "vk.cc",
        "on.soundcloud.com",
        "soundcloud.app.goo.gl",
        "spotify.link",
        "spoti.fi",
    }
)
_MAX_HOPS: Final = 5


class HttpxUrlExpander:
    """Реализация shared_kernel.UrlExpander поверх httpx, редиректы — вручную."""

    def __init__(self, client: httpx.AsyncClient, timeout_seconds: float = 5.0) -> None:
        self._client = client
        self._timeout = timeout_seconds

    def is_short_link(self, host: str) -> bool:
        return host.lower() in SHORT_LINK_HOSTS

    async def expand(self, url: str) -> str:
        current = url
        for _ in range(_MAX_HOPS):
            parts = urlsplit(current)
            host = (parts.hostname or "").lower()
            if not self.is_short_link(host):
                return current  # дошли до площадки (или чужого хоста — его отвергнет парсер)
            # Сокращатели отвечают и по https; http не используем, даже если пришёл в ссылке.
            current = urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))
            location = await self._redirect_target(current)
            current = urljoin(current, location)
        raise UnsupportedLinkError("short_link_unresolved", "Слишком длинная цепочка редиректов")

    async def _redirect_target(self, url: str) -> str:
        try:
            # GET, а не HEAD: часть сокращателей на HEAD отвечает 405 или 200 без
            # редиректа. Тело не читаем — нужен только заголовок Location.
            async with self._client.stream(
                "GET", url, follow_redirects=False, timeout=self._timeout
            ) as response:
                location: str | None = response.headers.get("location")
                if not response.is_redirect or not location:
                    raise UnsupportedLinkError(
                        "short_link_unresolved", "Короткая ссылка никуда не ведёт"
                    )
                return location
        except httpx.HTTPError as exc:
            raise UnsupportedLinkError(
                "short_link_unresolved", "Не удалось раскрыть короткую ссылку"
            ) from exc
