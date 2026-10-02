from collections.abc import AsyncIterator
from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException
from redis.asyncio import Redis
from sse_starlette.sse import EventSourceResponse

from syncplaylists.modules.transfers.application.use_cases import (
    GetTransferUseCase,
    ResolveUncertainItemUseCase,
    StartTransferUseCase,
)
from syncplaylists.modules.transfers.presentation.schemas import (
    ResolveItemRequest,
    StartTransferRequest,
    TransferResponse,
)

router = APIRouter(prefix="/transfers", tags=["transfers"])


@router.post("", status_code=201)
@inject
async def start_transfer(
    request: StartTransferRequest, use_case: FromDishka[StartTransferUseCase]
) -> TransferResponse:
    dto = await use_case.execute(
        request.user_id, request.source.to_domain(), request.destination.to_domain()
    )
    return TransferResponse.from_dto(dto)


@router.get("/{transfer_id}")
@inject
async def get_transfer(
    transfer_id: UUID, use_case: FromDishka[GetTransferUseCase]
) -> TransferResponse:
    dto = await use_case.execute(transfer_id)
    if dto is None:
        raise HTTPException(status_code=404, detail="Transfer не найден")
    return TransferResponse.from_dto(dto)


@router.post("/{transfer_id}/items/{position}/resolve", status_code=204)
@inject
async def resolve_item(
    transfer_id: UUID,
    position: int,
    request: ResolveItemRequest,
    use_case: FromDishka[ResolveUncertainItemUseCase],
) -> None:
    await use_case.execute(transfer_id, position, request.chosen_ref())


@router.get("/{transfer_id}/events")
@inject
async def transfer_events(
    transfer_id: UUID,
    get_transfer: FromDishka[GetTransferUseCase],
    redis: FromDishka[Redis],
) -> EventSourceResponse:
    dto = await get_transfer.execute(transfer_id)
    if dto is None:
        raise HTTPException(status_code=404, detail="Transfer не найден")

    async def stream() -> AsyncIterator[dict[str, str]]:
        # Снэпшот сразу — иначе клиент, подключившийся после начала переноса,
        # не увидит прогресс до следующего события (pubsub не хранит историю).
        yield {"event": "snapshot", "data": TransferResponse.from_dto(dto).model_dump_json()}
        channel = f"transfer:{transfer_id}"
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                # redis.asyncio без decode_responses отдаёт payload как bytes — иначе
                # sse_starlette сериализует его через str(b'...'), а не сам JSON.
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                yield {"event": "domain_event", "data": data}
        finally:
            await pubsub.unsubscribe(channel)

    return EventSourceResponse(stream())
