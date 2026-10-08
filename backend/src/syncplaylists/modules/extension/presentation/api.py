from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Header, HTTPException, Request, Response, status

from syncplaylists.modules.extension.application.dto import DeviceDto
from syncplaylists.modules.extension.application.ports import AttemptLimiter
from syncplaylists.modules.extension.application.use_cases import (
    AuthenticateDeviceUseCase,
    ClaimPairingUseCase,
    ConfirmPairingUseCase,
    ListDevicesUseCase,
    RevokeDeviceUseCase,
    StartPairingUseCase,
)
from syncplaylists.modules.extension.domain.errors import DeviceNotFoundError, PairingNotFoundError
from syncplaylists.modules.extension.presentation.schemas import (
    ClaimPairingRequest,
    ClaimPairingResponse,
    ConfirmPairingRequest,
    DeviceMeResponse,
    DeviceSchema,
    StartPairingRequest,
    StartPairingResponse,
)
from syncplaylists.modules.identity.application.use_cases import GetCurrentUserUseCase
from syncplaylists.modules.identity.presentation.dependencies import CurrentUserId

router = APIRouter(prefix="/extension", tags=["extension"])

_DEVICE_SCHEME = "Device "


async def _limit(limiter: AttemptLimiter, key: str) -> None:
    retry_after = await limiter.hit(key)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "too_many_attempts", "message": "Слишком много попыток"},
            headers={"Retry-After": str(retry_after)},
        )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/pairings", status_code=status.HTTP_201_CREATED)
@inject
async def start_pairing(
    body: StartPairingRequest,
    request: Request,
    use_case: FromDishka[StartPairingUseCase],
    limiter: FromDishka[AttemptLimiter],
) -> StartPairingResponse:
    """Шаг 1 (зовёт расширение, без входа): код для человека + секретный pairing_id."""
    await _limit(limiter, f"ext-pair:{_client_ip(request)}")
    started = await use_case.execute(
        device_name=body.device_name, browser=body.browser, version=body.version
    )
    return StartPairingResponse(
        pairing_id=started.pairing_id,
        user_code=started.user_code,
        expires_in=started.expires_in,
        interval=started.interval,
    )


@router.post("/pairings/confirm", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def confirm_pairing(
    body: ConfirmPairingRequest,
    user_id: CurrentUserId,
    use_case: FromDishka[ConfirmPairingUseCase],
    limiter: FromDishka[AttemptLimiter],
) -> None:
    """Шаг 2 (зовёт сайт, пользователь вошёл): ввод кода, который показало расширение."""
    await _limit(limiter, f"ext-confirm:{user_id}")
    try:
        await use_case.execute(user_id, body.user_code)
    except PairingNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            {"code": "pairing_not_found", "message": "Код неверный или истёк"},
        ) from exc


@router.post("/pairings/token")
@inject
async def claim_pairing(
    body: ClaimPairingRequest,
    response: Response,
    use_case: FromDishka[ClaimPairingUseCase],
) -> ClaimPairingResponse:
    """Шаг 3 (опрашивает расширение раз в interval): 202 — код ещё не введён; 200 —
    токен устройства (выдаётся один раз). pairing_id — в теле, а не в URL: это секрет,
    в логах прокси ему не место."""
    try:
        result = await use_case.execute(body.pairing_id)
    except PairingNotFoundError as exc:
        raise HTTPException(
            status.HTTP_410_GONE,
            {"code": "pairing_expired", "message": "Привязка истекла — начните заново"},
        ) from exc
    if result is None:
        response.status_code = status.HTTP_202_ACCEPTED
        return ClaimPairingResponse(status="pending")
    return ClaimPairingResponse(
        status="paired", device_id=result.device_id, device_token=result.device_token
    )


async def _authenticated_device(
    authenticate: AuthenticateDeviceUseCase, authorization: str | None
) -> DeviceDto:
    """Authorization: Device <токен устройства> → устройство, иначе 401."""
    token = (
        authorization.removeprefix(_DEVICE_SCHEME)
        if authorization and authorization.startswith(_DEVICE_SCHEME)
        else ""
    )
    device = await authenticate.execute(token)
    if device is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Устройство не привязано или отозвано")
    return device


@router.get("/me")
@inject
async def device_me(
    authenticate: FromDishka[AuthenticateDeviceUseCase],
    current_user: FromDishka[GetCurrentUserUseCase],
    authorization: Annotated[str | None, Header()] = None,
) -> DeviceMeResponse:
    """Для расширения: к какому аккаунту SyncPlaylists оно привязано."""
    device = await _authenticated_device(authenticate, authorization)
    user = await current_user.execute(device.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Устройство не привязано или отозвано")
    return DeviceMeResponse(device_id=device.id, user_email=user.email)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def unpair_self(
    authenticate: FromDishka[AuthenticateDeviceUseCase],
    revoke: FromDishka[RevokeDeviceUseCase],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Расширение отвязывает себя само (кнопка «Отвязать» в popup) — то же, что отзыв с
    сайта: токен устройства больше не принимается, WebSocket закроется на следующем ping."""
    device = await _authenticated_device(authenticate, authorization)
    try:
        await revoke.execute(device.user_id, device.id)
    except DeviceNotFoundError as exc:  # отозвали параллельно — цель достигнута
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Устройство уже отвязано") from exc


@router.get("/devices")
@inject
async def list_devices(
    user_id: CurrentUserId, use_case: FromDishka[ListDevicesUseCase]
) -> list[DeviceSchema]:
    return [DeviceSchema.from_dto(dto) for dto in await use_case.execute(user_id)]


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def revoke_device(
    device_id: UUID, user_id: CurrentUserId, use_case: FromDishka[RevokeDeviceUseCase]
) -> None:
    try:
        await use_case.execute(user_id, device_id)
    except DeviceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Устройство не найдено") from exc
