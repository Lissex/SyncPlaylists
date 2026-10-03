import logging
from datetime import timedelta
from typing import Any, Final
from uuid import UUID

from arq.worker import Retry
from dishka import FromDishka
from dishka.integrations.arq import inject

from syncplaylists.modules.transfers.application.use_cases import (
    FailTransferItemUseCase,
    MatchTransferItemUseCase,
    ProcessTransferUseCase,
    SweepStaleTransfersUseCase,
    WriteTransferUseCase,
)


@inject
async def run_transfer(
    ctx: dict[str, Any], transfer_id: UUID, use_case: FromDishka[ProcessTransferUseCase]
) -> None:
    await use_case.execute(transfer_id)


logger = logging.getLogger(__name__)

# Всего попыток run_match на один трек (первая + повторы). Не больше
# WorkerSettings.max_tries (по умолчанию в ARQ — 5), иначе ARQ оборвёт раньше.
MATCH_MAX_TRIES: Final = 3
_MATCH_RETRY_BASE_DELAY: Final = timedelta(seconds=5)


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
            raise Retry(defer=_MATCH_RETRY_BASE_DELAY * job_try) from exc
        logger.exception("run_match %s/%s: повторы исчерпаны, item → FAILED", transfer_id, position)
        await fail_item.execute(transfer_id, position, reason=type(exc).__name__)


@inject
async def run_write(
    ctx: dict[str, Any], transfer_id: UUID, use_case: FromDishka[WriteTransferUseCase]
) -> None:
    await use_case.execute(transfer_id)


@inject
async def sweep_stale_transfers(
    ctx: dict[str, Any], use_case: FromDishka[SweepStaleTransfersUseCase]
) -> None:
    await use_case.execute()
