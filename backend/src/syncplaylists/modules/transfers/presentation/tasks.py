from typing import Any
from uuid import UUID

from dishka import FromDishka
from dishka.integrations.arq import inject

from syncplaylists.modules.transfers.application.use_cases import (
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


@inject
async def run_match(
    ctx: dict[str, Any],
    transfer_id: UUID,
    position: int,
    use_case: FromDishka[MatchTransferItemUseCase],
) -> None:
    await use_case.execute(transfer_id, position)


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
