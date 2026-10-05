import logging
import random
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
    PauseTransferForClientUseCase,
    PauseTransferForQuotaUseCase,
    ProcessTransferUseCase,
    ResumeClientPausedTransfersUseCase,
    ResumeTransferUseCase,
    SweepStaleTransfersUseCase,
    WriteTransferUseCase,
)
from syncplaylists.shared_kernel.domain.errors import (
    ExtensionUnavailableError,
    PlatformError,
    PlatformRateLimitedError,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform

logger = logging.getLogger(__name__)

# Всего попыток run_match на один трек (первая + повторы) при сбоях.
MATCH_MAX_TRIES: Final = 3
# То же для run_transfer/run_write — повторяются только временные ошибки площадки.
PLATFORM_MAX_TRIES: Final = 3
# «Подождите» от площадки (429) или от нашего token bucket — не сбой: трек от этого не
# становится хуже. Короткое ожидание — обычный повтор задачи (свой бюджет попыток);
# долгое (квота площадки, Retry-After от QUOTA_PAUSE_MIN_SECONDS) или исчерпанный
# бюджет — пауза ВСЕГО переноса (PAUSED_QUOTA) до срока: ни один трек не уходит в FAILED
# из-за квоты (этап 4b-3). ARQ считает попытки общим счётчиком: WorkerSettings.max_tries
# должен быть не меньше RATE_LIMITED_MAX_TRIES.
RATE_LIMITED_MAX_TRIES: Final = 12
QUOTA_PAUSE_MIN_SECONDS: Final = 60.0
_MATCH_RETRY_BASE_DELAY: Final = timedelta(seconds=5)
_PLATFORM_UNAVAILABLE: Final = "platform_unavailable"


def _is_quota_pause(exc: BaseException, job_try: int) -> bool:
    return isinstance(exc, PlatformRateLimitedError) and (
        exc.retry_after_seconds >= QUOTA_PAUSE_MIN_SECONDS or job_try >= RATE_LIMITED_MAX_TRIES
    )


async def _pause(
    pause: PauseTransferForQuotaUseCase,
    task: str,
    transfer_id: UUID,
    exc: PlatformRateLimitedError,
) -> None:
    logger.warning(
        "%s %s: квота площадки (%s), перенос на паузе на %.0f с",
        task,
        transfer_id,
        exc,
        exc.retry_after_seconds,
    )
    await pause.execute(transfer_id, exc.retry_after_seconds)


async def _pause_for_client(
    pause_client: PauseTransferForClientUseCase,
    task: str,
    transfer_id: UUID,
    exc: ExtensionUnavailableError,
) -> None:
    logger.warning(
        "%s %s: расширение не может выполнить операцию (%s), перенос ждёт браузер",
        task,
        transfer_id,
        exc.reason.value,
    )
    await pause_client.execute(transfer_id, exc.reason.value)


def _max_tries(exc: BaseException, default: int) -> int:
    return RATE_LIMITED_MAX_TRIES if isinstance(exc, PlatformRateLimitedError) else default


def _retry_delay(job_try: int, exc: BaseException) -> timedelta:
    delay = _MATCH_RETRY_BASE_DELAY * job_try
    if isinstance(exc, PlatformRateLimitedError):
        # Площадка (или token bucket) сказала, сколько ждать — не раньше этого. Плюс
        # немного разброса: десятки отложенных задач не должны проснуться в одну секунду.
        wait = timedelta(seconds=exc.retry_after_seconds * random.uniform(1.0, 1.2))
        delay = max(_MATCH_RETRY_BASE_DELAY, wait)
    return delay


@inject
async def run_transfer(
    ctx: dict[str, Any],
    transfer_id: UUID,
    use_case: FromDishka[ProcessTransferUseCase],
    fail_transfer: FromDishka[FailTransferUseCase],
    pause: FromDishka[PauseTransferForQuotaUseCase],
    pause_client: FromDishka[PauseTransferForClientUseCase],
) -> None:
    await with_platform_retries(
        int(ctx.get("job_try", 1)),
        "run_transfer",
        transfer_id,
        use_case.execute,
        fail_transfer,
        pause,
        pause_client=pause_client,
    )


async def with_platform_retries(
    job_try: int,
    task: str,
    transfer_id: UUID,
    execute: Callable[[UUID], Awaitable[None]],
    fail_transfer: FailTransferUseCase,
    pause: PauseTransferForQuotaUseCase,
    *,
    pause_client: PauseTransferForClientUseCase | None = None,
) -> None:
    """run_transfer/run_write: временная ошибка площадки (сеть, 5xx, rate limit, разовый
    401) — ограниченный повтор; после последней попытки перенос FAILED. Терминальные
    ошибки (токен протух, плейлист чужой) use case обрабатывает сам и не пробрасывает.
    Расширение недоступно (браузер закрыт и т.п.) — не ошибка: перенос ждёт браузер."""
    try:
        await execute(transfer_id)
    except PlatformError as exc:
        if isinstance(exc, ExtensionUnavailableError) and pause_client is not None:
            await _pause_for_client(pause_client, task, transfer_id, exc)
            return
        if isinstance(exc, PlatformRateLimitedError) and _is_quota_pause(exc, job_try):
            await _pause(pause, task, transfer_id, exc)
            return
        if job_try < _max_tries(exc, PLATFORM_MAX_TRIES):
            # str(exc) — код и имя ошибки площадки, без токенов.
            logger.warning(
                "%s %s: попытка %s не удалась (%s: %s), повтор",
                task,
                transfer_id,
                job_try,
                type(exc).__name__,
                exc,
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
    pause: FromDishka[PauseTransferForQuotaUseCase],
    pause_client: FromDishka[PauseTransferForClientUseCase],
) -> None:
    await match_with_retries(
        int(ctx.get("job_try", 1)),
        transfer_id,
        position,
        use_case,
        fail_item,
        pause,
        pause_client=pause_client,
    )


async def match_with_retries(
    job_try: int,
    transfer_id: UUID,
    position: int,
    use_case: MatchTransferItemUseCase,
    fail_item: FailTransferItemUseCase,
    pause: PauseTransferForQuotaUseCase,
    *,
    pause_client: PauseTransferForClientUseCase | None = None,
) -> None:
    try:
        await use_case.execute(transfer_id, position)
    except Exception as exc:
        if isinstance(exc, ExtensionUnavailableError) and pause_client is not None:
            # Трек остаётся PENDING; его поставит заново resume_client_transfers.
            await _pause_for_client(pause_client, f"run_match/{position}", transfer_id, exc)
            return
        if isinstance(exc, PlatformRateLimitedError) and _is_quota_pause(exc, job_try):
            # Трек остаётся PENDING; его (и остальные) поставит заново resume_transfer.
            await _pause(pause, f"run_match/{position}", transfer_id, exc)
            return
        # ARQ сам не повторяет джобу на обычном исключении — без этого упавший трек
        # навсегда оставил бы перенос в RUNNING с вечным PENDING. Транзакция use case
        # к этому моменту уже откачена (UnitOfWork.__aexit__).
        if job_try < _max_tries(exc, MATCH_MAX_TRIES):
            logger.warning(
                "run_match %s/%s: попытка %s не удалась (%s: %s), повтор",
                transfer_id,
                position,
                job_try,
                type(exc).__name__,
                exc,
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
    pause: FromDishka[PauseTransferForQuotaUseCase],
    pause_client: FromDishka[PauseTransferForClientUseCase],
) -> None:
    await with_platform_retries(
        int(ctx.get("job_try", 1)),
        "run_write",
        transfer_id,
        use_case.execute,
        fail_transfer,
        pause,
        pause_client=pause_client,
    )


@inject
async def resume_transfer(
    ctx: dict[str, Any], transfer_id: UUID, use_case: FromDishka[ResumeTransferUseCase]
) -> None:
    await use_case.execute(transfer_id)


@inject
async def resume_client_transfers(
    ctx: dict[str, Any],
    user_id: UUID,
    platform: str,
    use_case: FromDishka[ResumeClientPausedTransfersUseCase],
) -> None:
    """Расширение пользователя снова готово работать с площадкой — продолжить его
    переносы, ждущие браузер. Ставит контекст extension (по имени задачи из
    shared_kernel.application.tasks)."""
    await use_case.execute(user_id, Platform(platform))


@inject
async def sweep_stale_transfers(
    ctx: dict[str, Any], use_case: FromDishka[SweepStaleTransfersUseCase]
) -> None:
    await use_case.execute()
