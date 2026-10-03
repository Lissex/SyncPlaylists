from collections.abc import AsyncIterator
from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, status
from redis.asyncio import Redis
from sse_starlette.sse import EventSourceResponse

from syncplaylists.modules.identity.presentation.dependencies import CurrentUserId
from syncplaylists.modules.transfers.application.links import ResolvePlaylistLinkUseCase
from syncplaylists.modules.transfers.application.use_cases import (
    GetTransferUseCase,
    ResolveUncertainItemUseCase,
    StartTransferUseCase,
    TransferNotFoundError,
)
from syncplaylists.modules.transfers.domain.value_objects import TrackDestination, TrackSource
from syncplaylists.modules.transfers.presentation.schemas import (
    LinkSchema,
    ResolvedLinkResponse,
    ResolveItemRequest,
    ResolveLinkRequest,
    StartTransferRequest,
    TransferResponse,
)
from syncplaylists.shared_kernel.application.ports import AccountNotAvailableError
from syncplaylists.shared_kernel.domain.errors import (
    PlatformError,
    PlatformNotSupportedError,
    PlatformRegionError,
    PlaylistNotFoundError,
    PlaylistNotWritableError,
    UnsupportedLinkError,
)

router = APIRouter(prefix="/transfers", tags=["transfers"])
links_router = APIRouter(prefix="/links", tags=["links"])


def _unprocessable(code: str, message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": code, "message": message})


def _link_or_platform_error(exc: Exception) -> HTTPException:
    """Ошибки разбора ссылки и обращения к площадке → HTTP с машиночитаемым code."""
    if isinstance(exc, UnsupportedLinkError):
        return _unprocessable(exc.reason, str(exc))
    if isinstance(exc, PlatformNotSupportedError):
        return _unprocessable("platform_not_supported", str(exc))
    if isinstance(exc, AccountNotAvailableError):
        return _unprocessable(
            "account_not_connected",
            "Нет подключённого активного аккаунта для площадки источника или назначения",
        )
    if isinstance(exc, PlaylistNotFoundError):
        return _unprocessable("playlist_not_found", "Плейлист не найден или закрыт")
    if isinstance(exc, PlaylistNotWritableError):
        return _unprocessable(
            "playlist_not_writable", "Добавлять треки можно только в свой плейлист"
        )
    if isinstance(exc, PlatformRegionError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {"code": "region_blocked", "message": "Площадка недоступна из региона сервера"},
        )
    assert isinstance(exc, PlatformError)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        {"code": "platform_unavailable", "message": "Площадка не ответила, попробуйте позже"},
    )


_HANDLED_ERRORS = (
    UnsupportedLinkError,
    PlatformNotSupportedError,
    AccountNotAvailableError,
    PlatformError,
)


async def _resolve_request(
    request: StartTransferRequest, user_id: UUID, resolve_link: ResolvePlaylistLinkUseCase
) -> tuple[TrackSource, TrackDestination]:
    if isinstance(request.source, LinkSchema):
        source: TrackSource = (await resolve_link.execute(user_id, request.source.url)).as_source()
    else:
        source = request.source.to_domain()
    if isinstance(request.destination, LinkSchema):
        resolved = await resolve_link.execute(user_id, request.destination.url)
        destination: TrackDestination = resolved.as_destination()
    else:
        destination = request.destination.to_domain()
    return source, destination


@router.post("", status_code=201)
@inject
async def start_transfer(
    request: StartTransferRequest,
    user_id: CurrentUserId,
    use_case: FromDishka[StartTransferUseCase],
    resolve_link: FromDishka[ResolvePlaylistLinkUseCase],
) -> TransferResponse:
    try:
        source, destination = await _resolve_request(request, user_id, resolve_link)
        dto = await use_case.execute(user_id, source, destination)
    except _HANDLED_ERRORS as exc:
        raise _link_or_platform_error(exc) from exc
    return TransferResponse.from_dto(dto)


@links_router.post("/resolve")
@inject
async def resolve_link(
    request: ResolveLinkRequest,
    user_id: CurrentUserId,
    use_case: FromDishka[ResolvePlaylistLinkUseCase],
) -> ResolvedLinkResponse:
    """Предпросмотр ссылки для фронта: площадка, плейлист или медиатека, название."""
    try:
        dto = await use_case.execute(user_id, request.url)
    except _HANDLED_ERRORS as exc:
        raise _link_or_platform_error(exc) from exc
    return ResolvedLinkResponse.from_dto(dto)


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
