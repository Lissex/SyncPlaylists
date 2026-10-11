import base64
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Final
from uuid import UUID

from dishka import AsyncContainer
from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Query, Request, status
from redis.asyncio import Redis
from sse_starlette.sse import EventSourceResponse

from syncplaylists.modules.identity.presentation.dependencies import CurrentUserId
from syncplaylists.modules.transfers.application.links import ResolvePlaylistLinkUseCase
from syncplaylists.modules.transfers.application.ports import TransferCursor, TransferSummary
from syncplaylists.modules.transfers.application.use_cases import (
    GetTransferProgressUseCase,
    GetTransferUseCase,
    ListTransferItemsUseCase,
    ListTransfersUseCase,
    ResolveUncertainItemUseCase,
    StartTransferUseCase,
    StartWritingUseCase,
    TransferNotFoundError,
)
from syncplaylists.modules.transfers.domain.errors import InvalidTransferTransitionError
from syncplaylists.modules.transfers.domain.value_objects import (
    TrackDestination,
    TrackSource,
    TransferItemStatus,
)
from syncplaylists.modules.transfers.presentation.schemas import (
    LinkSchema,
    ResolvedLinkResponse,
    ResolveItemRequest,
    ResolveLinkRequest,
    StartTransferRequest,
    TransferHistoryPage,
    TransferItemResponse,
    TransferItemsPage,
    TransferProgressSchema,
    TransferResponse,
    TransferSummaryResponse,
)
from syncplaylists.shared_kernel.application.ports import AccountNotAvailableError
from syncplaylists.shared_kernel.domain.errors import (
    ExtensionUnavailableError,
    PlatformError,
    PlatformNotSupportedError,
    PlatformRegionError,
    PlaylistNotFoundError,
    PlaylistNotWritableError,
    UnsupportedLinkError,
    WriteRequiresExtensionError,
)

router = APIRouter(prefix="/transfers", tags=["transfers"])
links_router = APIRouter(prefix="/links", tags=["links"])


logger = logging.getLogger(__name__)


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
    if isinstance(exc, WriteRequiresExtensionError):
        return _unprocessable(
            "write_requires_extension",
            "Писать на эту площадку можно только через браузерное расширение — "
            "подключите её в расширении",
        )
    if isinstance(exc, PlatformRegionError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {"code": "region_blocked", "message": "Площадка недоступна из региона сервера"},
        )
    if isinstance(exc, ExtensionUnavailableError):
        # Транспорт EXTENSION: проверку (например, права записи в плейлист) делает
        # браузер пользователя, а он сейчас недоступен — не сбой площадки.
        return HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "extension_unavailable",
                "reason": exc.reason.value,
                "message": "Расширение в браузере сейчас недоступно",
            },
        )
    assert isinstance(exc, PlatformError)
    # Причина — в лог: без неё «площадка не ответила» не отличить от сбоя схемы ответа
    # расширения. str(exc) — код и имя ошибки площадки, без токенов и данных ответа.
    logger.warning("Ссылка/перенос: ошибка площадки %s: %s", type(exc).__name__, exc)
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
) -> tuple[TrackSource, TrackDestination, str | None]:
    source_title: str | None = None
    if isinstance(request.source, LinkSchema):
        resolved_source = await resolve_link.execute(user_id, request.source.url, preview=False)
        source: TrackSource = resolved_source.as_source()
        source_title = resolved_source.title
    else:
        source = request.source.to_domain()
    if isinstance(request.destination, LinkSchema):
        resolved = await resolve_link.execute(user_id, request.destination.url, preview=False)
        destination: TrackDestination = resolved.as_destination()
    else:
        destination = request.destination.to_domain()
    return source, destination, source_title


@router.post("", status_code=201)
@inject
async def start_transfer(
    request: StartTransferRequest,
    user_id: CurrentUserId,
    use_case: FromDishka[StartTransferUseCase],
    resolve_link: FromDishka[ResolvePlaylistLinkUseCase],
) -> TransferResponse:
    try:
        source, destination, source_title = await _resolve_request(request, user_id, resolve_link)
        dto = await use_case.execute(user_id, source, destination, source_title)
    except _HANDLED_ERRORS as exc:
        raise _link_or_platform_error(exc) from exc
    return TransferResponse.from_dto(dto)


def _encode_cursor(summary: TransferSummary) -> str:
    raw = f"{summary.created_at.isoformat()}|{summary.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> TransferCursor:
    try:
        created_at, transfer_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return TransferCursor(datetime.fromisoformat(created_at), UUID(transfer_id))
    except ValueError as exc:  # binascii.Error — тоже ValueError
        raise _unprocessable("bad_cursor", "Неверный курсор страницы") from exc


@router.get("")
@inject
async def list_transfers(
    user_id: CurrentUserId,
    use_case: FromDishka[ListTransfersUseCase],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TransferHistoryPage:
    """История переносов пользователя, новые первыми."""
    before = _decode_cursor(cursor) if cursor else None
    summaries = await use_case.execute(user_id, before, limit + 1)
    page, more = summaries[:limit], len(summaries) > limit
    return TransferHistoryPage(
        items=[TransferSummaryResponse.from_summary(s) for s in page],
        next_cursor=_encode_cursor(page[-1]) if more else None,
    )


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
    transfer_id: UUID,
    user_id: CurrentUserId,
    use_case: FromDishka[GetTransferUseCase],
    include_items: bool = True,
) -> TransferResponse:
    """include_items=false — только шапка со счётчиками (экран переноса берёт треки
    постранично через /items)."""
    # Чужой перенос — тот же 404, что и несуществующий: не подтверждаем, что id есть.
    dto = await use_case.execute(user_id, transfer_id, with_items=include_items)
    if dto is None:
        raise HTTPException(status_code=404, detail="Transfer не найден")
    return TransferResponse.from_dto(dto)


@router.get("/{transfer_id}/items")
@inject
async def list_transfer_items(
    transfer_id: UUID,
    user_id: CurrentUserId,
    use_case: FromDishka[ListTransferItemsUseCase],
    status_: Annotated[list[TransferItemStatus] | None, Query(alias="status")] = None,
    after: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TransferItemsPage:
    """Треки переноса постранично: ревью (`status=uncertain&status=not_found`) и отчёт."""
    dtos = await use_case.execute(user_id, transfer_id, status_ or (), after, limit + 1)
    if dtos is None:
        raise HTTPException(status_code=404, detail="Transfer не найден")
    page, more = dtos[:limit], len(dtos) > limit
    return TransferItemsPage(
        items=[TransferItemResponse.from_dto(dto) for dto in page],
        next_after=page[-1].position if more else None,
    )


@router.post("/{transfer_id}/write", status_code=204)
@inject
async def start_writing(
    transfer_id: UUID, user_id: CurrentUserId, use_case: FromDishka[StartWritingUseCase]
) -> None:
    """«Записать в <площадку>» после ревью. Не решённые сомнительные треки пропускаются."""
    try:
        await use_case.execute(user_id, transfer_id)
    except TransferNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Transfer не найден") from exc
    except InvalidTransferTransitionError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "not_in_review", "message": "Перенос не ждёт ревью — запись уже идёт"},
        ) from exc


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
    except InvalidTransferTransitionError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "item_not_resolvable", "message": "Этот трек сейчас нельзя изменить"},
        ) from exc


# Как часто SSE шлёт событие `progress` (счётчики + ETA) — и при потоке событий, и
# когда их нет вовсе (пауза площадки: ETA при этом растёт, клиент это видит).
PROGRESS_INTERVAL_SECONDS: Final = 3.0
_FINISHED_STATUSES: Final = frozenset({"done", "failed"})


async def _progress(
    container: AsyncContainer, user_id: UUID, transfer_id: UUID
) -> TransferProgressSchema | None:
    # Стрим живёт дольше запроса — зависимости запроса к этому моменту уже закрыты,
    # поэтому на каждый опрос — свой короткий request-scope (своя сессия БД).
    async with container() as request_container:
        use_case = await request_container.get(GetTransferProgressUseCase)
        dto = await use_case.execute(user_id, transfer_id)
    return TransferProgressSchema.from_dto(dto) if dto is not None else None


@router.get("/{transfer_id}/events")
@inject
async def transfer_events(
    transfer_id: UUID,
    user_id: CurrentUserId,
    request: Request,
    get_transfer: FromDishka[GetTransferUseCase],
    redis: FromDishka[Redis],
) -> EventSourceResponse:
    # EventSource на том же origin шлёт cookie сам; с другого — withCredentials: true.
    # Снэпшот — без items: на тысячах треков они весят мегабайты, сайт берёт их
    # постранично (GET /transfers/{id}/items).
    dto = await get_transfer.execute(user_id, transfer_id, with_items=False)
    if dto is None:
        raise HTTPException(status_code=404, detail="Transfer не найден")
    app_container: AsyncContainer = request.app.state.dishka_container

    async def stream() -> AsyncIterator[dict[str, str]]:
        # Снэпшот сразу — иначе клиент, подключившийся после начала переноса,
        # не увидит прогресс до следующего события (pubsub не хранит историю).
        yield {"event": "snapshot", "data": TransferResponse.from_dto(dto).model_dump_json()}
        channel = f"transfer:{transfer_id}"
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        last_progress = time.monotonic()
        finished = dto.status in _FINISHED_STATUSES
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is not None and message["type"] == "message":
                    # redis.asyncio без decode_responses отдаёт payload как bytes — иначе
                    # sse_starlette сериализует его через str(b'...'), а не сам JSON.
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    yield {"event": "domain_event", "data": data}
                if finished or time.monotonic() - last_progress < PROGRESS_INTERVAL_SECONDS:
                    continue
                last_progress = time.monotonic()
                progress = await _progress(app_container, user_id, transfer_id)
                if progress is not None:
                    yield {"event": "progress", "data": progress.model_dump_json()}
                    # После done/failed прогресс не меняется — дальше только события.
                    finished = progress.status in _FINISHED_STATUSES
        finally:
            await pubsub.unsubscribe(channel)

    return EventSourceResponse(stream())
