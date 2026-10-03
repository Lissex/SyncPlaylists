"""Строка лога при 429 площадки — одна на все адаптеры.

Только числа и account_id, без токенов. Аккаунт vs весь сервер (IP) — по этой паре видно,
на что считается квота площадки: если 429 приходит при малом числе запросов аккаунта,
но большом с сервера — квота на IP.
"""

import logging
from uuid import UUID

from syncplaylists.shared_kernel.application.ports import PlatformRateLimiter
from syncplaylists.shared_kernel.domain.value_objects import Platform


async def log_rate_limited(
    logger: logging.Logger,
    limiter: PlatformRateLimiter,
    platform: Platform,
    account_id: UUID | None,
    retry_after_seconds: float,
    detail: str = "",
) -> None:
    name = platform.value
    suffix = f", {detail}" if detail else ""
    try:
        account_10 = account_60 = None
        if account_id is not None:
            account_10 = await limiter.recent_requests(platform, account_id, 10)
            account_60 = await limiter.recent_requests(platform, account_id, 60)
        server_10 = await limiter.recent_requests(platform, None, 10)
        server_60 = await limiter.recent_requests(platform, None, 60)
    except Exception as exc:  # статистика не должна мешать обработке 429
        logger.warning(
            "%s 429 (аккаунт %s), счётчик недоступен: %s%s",
            name,
            account_id,
            type(exc).__name__,
            suffix,
        )
        return
    logger.warning(
        "%s 429 (аккаунт %s): аккаунт %s/%s, сервер (IP) %s/%s запросов за 10/60 мин,"
        " Retry-After %.0f с%s",
        name,
        account_id,
        "-" if account_10 is None else account_10,
        "-" if account_60 is None else account_60,
        server_10,
        server_60,
        retry_after_seconds,
        suffix,
    )
