from collections.abc import AsyncIterator
from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, status
from redis.asyncio import Redis
from sse_starlette.sse import EventSourceResponse

from syncplaylists.modules.identity.presentation.dependencies import CurrentUserId
from syncplaylists.modules.transfers.application.use_cases import (
    GetTransferUseCase,
    ResolveUncertainItemUseCase,
    StartTransferUseCase,
    TransferNotFoundError,
)
from syncplaylists.modules.transfers.presentation.schemas import (
    ResolveItemRequest,
    StartTransferRequest,
    TransferResponse,
)
from syncplaylists.shared_kernel.application.ports import AccountNotAvailableError

router = APIRouter(prefix="/transfers", tags=["transfers"])


@router.post("", status_code=201)
@inject
async def start_transfer(
    request: StartTransferRequest,
    user_id: CurrentUserId,
    use_case: FromDishka[StartTransferUseCase],
) -> TransferResponse:
    try:
        dto = await use_case.execute(
            user_id, request.source.to_domain(), request.destination.to_domain()
        )
    except AccountNotAvailableError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Нет подключённого активного аккаунта для площадки источника или назначения",
        ) from exc
    return TransferResponse.from_dto(dto)


@router.get("/{transfer_id}")
@inject
async def get_transfer(
    transfer_id: UUID, user_id: CurrentUserId, use_case: FromDishka[GetTransferUseCase]
) -> TransferResponse:
    # Чужой перенос — тот же 404, что и несуществующий: не подтверждаем, что id есть.
    dto = await use_case.execute(user_id, transfer_id)
    if dto is None:
        raise HTTPException(status_code=404, detail="Transfer не найден")
    return TransferResponse.from_dto(dto)


@router.post("/{transfer_id}/items/{position}/resolve", status_code=204)
@inject
async def resolve_item(
    transfer_id: UUID,
    position: int,
    request: ResolveItemRequest,
    user_id: CurrentUserId,
    use_case: FromDishka[ResolveUncertainItemUseCase],
) -> None:
    try:
        await use_case.execute(user_id, transfer_id, position, request.chosen_ref())
    except TransferNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Transfer не найден") from exc


@router.get("/{transfer_id}/events")
@inject
async def transfer_events(
    transfer_id: UUID,
    user_id: CurrentUserId,
    get_transfer: FromDishka[GetTransferUseCase],
    redis: FromDishka[Redis],
) -> EventSourceResponse:
    # EventSource на том же origin шлёт cookie сам; с другого — withCredentials: true.
    dto = await get_transfer.execute(user_id, transfer_id)
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
