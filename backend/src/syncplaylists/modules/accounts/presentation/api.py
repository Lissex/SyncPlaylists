from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse

from syncplaylists.modules.accounts.application.use_cases import (
    CompleteOAuthUseCase,
    ConnectAccountUseCase,
    DisconnectAccountUseCase,
    ListAccountsUseCase,
    OAuthFlowError,
    OAuthProviderNotFoundError,
    StartOAuthUseCase,
)
from syncplaylists.modules.accounts.domain.errors import (
    AccountAlreadyConnectedError,
    AccountNotFoundError,
    InvalidPlatformTokenError,
)
from syncplaylists.modules.accounts.presentation.schemas import (
    AccountResponse,
    ConnectAccountRequest,
)
from syncplaylists.modules.identity.presentation.dependencies import CurrentUserId
from syncplaylists.shared_kernel.application.ports import PlatformCredentials
from syncplaylists.shared_kernel.domain.errors import (
    PlatformError,
    PlatformNotSupportedError,
    PlatformRegionError,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform

router = APIRouter(prefix="/accounts", tags=["accounts"])


@dataclass(frozen=True, slots=True)
class FrontendRedirect:
    """Куда вернуть браузер после OAuth callback. Собирается в bootstrap из Settings."""

    url: str

    def to(self, **params: str) -> RedirectResponse:
        return RedirectResponse(f"{self.url}?{urlencode(params)}", status_code=302)


@router.get("")
@inject
async def list_accounts(
    user_id: CurrentUserId, use_case: FromDishka[ListAccountsUseCase]
) -> list[AccountResponse]:
    return [AccountResponse.from_dto(dto) for dto in await use_case.execute(user_id)]


@router.post("", status_code=status.HTTP_201_CREATED)
@inject
async def connect_account(
    body: ConnectAccountRequest,
    user_id: CurrentUserId,
    use_case: FromDishka[ConnectAccountUseCase],
) -> AccountResponse:
    try:
        dto = await use_case.execute(
            user_id=user_id,
            platform=body.platform,
            transport=body.transport,
            credentials=PlatformCredentials(
                access_token=body.access_token.get_secret_value(),
                refresh_token=(
                    body.refresh_token.get_secret_value() if body.refresh_token else None
                ),
                expires_at=body.expires_at,
            ),
        )
    except AccountAlreadyConnectedError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "На этой площадке уже подключён другой аккаунт"
        ) from exc
    except InvalidPlatformTokenError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"code": "invalid_token", "message": "Площадка не приняла токен"},
        ) from exc
    except PlatformNotSupportedError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"code": "platform_not_supported", "message": str(exc)},
        ) from exc
    except PlatformRegionError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {"code": "region_blocked", "message": "Площадка недоступна из региона сервера"},
        ) from exc
    except PlatformError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {"code": "platform_unavailable", "message": "Площадка не ответила, попробуйте позже"},
        ) from exc
    return AccountResponse.from_dto(dto)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def disconnect_account(
    account_id: UUID,
    user_id: CurrentUserId,
    use_case: FromDishka[DisconnectAccountUseCase],
) -> None:
    try:
        await use_case.execute(user_id, account_id)
    except AccountNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Аккаунт не найден") from exc


@router.get("/{platform}/oauth/start")
@inject
async def oauth_start(
    platform: Platform,
    user_id: CurrentUserId,
    use_case: FromDishka[StartOAuthUseCase],
) -> RedirectResponse:
    try:
        url = await use_case.execute(user_id, platform)
    except OAuthProviderNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"OAuth для {platform.value} не настроен"
        ) from exc
    return RedirectResponse(url, status_code=302)


@router.get("/{platform}/oauth/callback")
@inject
async def oauth_callback(
    platform: Platform,
    code: str,
    state: str,
    user_id: CurrentUserId,
    use_case: FromDishka[CompleteOAuthUseCase],
    frontend: FromDishka[FrontendRedirect],
) -> RedirectResponse:
    # Cookie сессии сюда доходит: SameSite=Lax отправляется при top-level GET-навигации
    # (редирект с площадки обратно на нас).
    try:
        await use_case.execute(user_id, platform, state, code)
    except (OAuthFlowError, OAuthProviderNotFoundError):
        return frontend.to(error="oauth_failed", platform=platform.value)
    except AccountAlreadyConnectedError:
        return frontend.to(error="already_connected", platform=platform.value)
    return frontend.to(connected=platform.value)
