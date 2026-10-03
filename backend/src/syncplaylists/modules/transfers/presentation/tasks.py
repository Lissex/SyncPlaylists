import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, Final
from uuid import UUID

from arq.worker import Retry
from dishka import FromDishka
from dishka.integrations.arq import inject

from syncplaylists.modules.transfers.application.use_cases import (
    FailTransferItemUseCase,
    FailTransferUseCase,
    MatchTransferItemUseCase,
    ProcessTransferUseCase,
    SweepStaleTransfersUseCase,
    WriteTransferUseCase,
)
from syncplaylists.shared_kernel.domain.errors import PlatformError, PlatformRateLimitedError

logger = logging.getLogger(__name__)

# Всего попыток run_match на один трек (первая + повторы). Не больше
# WorkerSettings.max_tries (по умолчанию в ARQ — 5), иначе ARQ оборвёт раньше.
MATCH_MAX_TRIES: Final = 3
# То же для run_transfer/run_write — повторяются только временные ошибки площадки.
PLATFORM_MAX_TRIES: Final = 3
_MATCH_RETRY_BASE_DELAY: Final = timedelta(seconds=5)
_PLATFORM_UNAVAILABLE: Final = "platform_unavailable"


def _retry_delay(job_try: int, exc: BaseException) -> timedelta:
    delay = _MATCH_RETRY_BASE_DELAY * job_try
    # Площадка (или наш token bucket) сказала, сколько ждать — не раньше этого.
    if isinstance(exc, PlatformRateLimitedError):
        delay = max(delay, timedelta(seconds=exc.retry_after_seconds))
    return delay


@inject
async def run_transfer(
    ctx: dict[str, Any],
    transfer_id: UUID,
    use_case: FromDishka[ProcessTransferUseCase],
    fail_transfer: FromDishka[FailTransferUseCase],
) -> None:
    await with_platform_retries(
        int(ctx.get("job_try", 1)), "run_transfer", transfer_id, use_case.execute, fail_transfer
    )


async def with_platform_retries(
    job_try: int,
    task: str,
    transfer_id: UUID,
    execute: Callable[[UUID], Awaitable[None]],
    fail_transfer: FailTransferUseCase,
) -> None:
    """run_transfer/run_write: временная ошибка площадки (сеть, 5xx, rate limit, разовый
    401) — ограниченный повтор; после последней попытки перенос FAILED. Терминальные
    ошибки (токен протух, плейлист чужой) use case обрабатывает сам и не пробрасывает."""
    try:
        await execute(transfer_id)
    except PlatformError as exc:
        if job_try < PLATFORM_MAX_TRIES:
            logger.warning(
                "%s %s: попытка %s не удалась (%s), повтор",
                task,
                transfer_id,
                job_try,
                type(exc).__name__,
            )
            raise Retry(defer=_retry_delay(job_try, exc)) from exc
        logger.exception("%s %s: повторы исчерпаны, перенос → FAILED", task, transfer_id)
        await fail_transfer.execute(transfer_id, _PLATFORM_UNAVAILABLE)


@inject
async def run_match(
    ctx: dict[str, Any],
    transfer_id: UUID,
    position: int,
    use_case: FromDishka[MatchTransferItemUseCase],
    fail_item: FromDishka[FailTransferItemUseCase],
) -> None:
    await match_with_retries(int(ctx.get("job_try", 1)), transfer_id, position, use_case, fail_item)


async def match_with_retries(
    job_try: int,
    transfer_id: UUID,
    position: int,
    use_case: MatchTransferItemUseCase,
    fail_item: FailTransferItemUseCase,
) -> None:
    try:
        await use_case.execute(transfer_id, position)
    except Exception as exc:
        # ARQ сам не повторяет джобу на обычном исключении — без этого упавший трек
        # навсегда оставил бы перенос в RUNNING с вечным PENDING. Транзакция use case
        # к этому моменту уже откачена (UnitOfWork.__aexit__).
        if job_try < MATCH_MAX_TRIES:
            logger.warning(
                "run_match %s/%s: попытка %s не удалась (%s), повтор",
                transfer_id,
                position,
                job_try,
                type(exc).__name__,
            )
            raise Retry(defer=_retry_delay(job_try, exc)) from exc
        logger.exception("run_match %s/%s: повторы исчерпаны, item → FAILED", transfer_id, position)
        await fail_item.execute(transfer_id, position, reason=type(exc).__name__)


@inject
async def run_write(
    ctx: dict[str, Any],
    transfer_id: UUID,
    use_case: FromDishka[WriteTransferUseCase],
    fail_transfer: FromDishka[FailTransferUseCase],
) -> None:
    await with_platform_retries(
        int(ctx.get("job_try", 1)), "run_write", transfer_id, use_case.execute, fail_transfer
    )


@inject
async def sweep_stale_transfers(
    ctx: dict[str, Any], use_case: FromDishka[SweepStaleTransfersUseCase]
) -> None:
    await use_case.execute()
