from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Request, Response, status

from syncplaylists.modules.identity.application.use_cases import (
    GetCurrentUserUseCase,
    LoginUseCase,
    LogoutAllUseCase,
    LogoutUseCase,
    RegisterUserUseCase,
    WeakPasswordError,
)
from syncplaylists.modules.identity.domain.errors import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidEmailError,
    TooManyAttemptsError,
)
from syncplaylists.modules.identity.presentation.cookies import SessionCookiePolicy
from syncplaylists.modules.identity.presentation.dependencies import CurrentUserId
from syncplaylists.modules.identity.presentation.schemas import (
    CredentialsRequest,
    LoginRequest,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    # За обратным прокси нужен uvicorn --proxy-headers --forwarded-allow-ips, иначе
    # здесь будет адрес прокси и rate limit станет общим на всех (ARCHITECTURE.md, 11b).
    return request.client.host if request.client is not None else "unknown"


def _too_many(exc: TooManyAttemptsError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Слишком много попыток, попробуйте позже",
        headers={"Retry-After": str(exc.retry_after_s)},
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
@inject
async def register(
    body: CredentialsRequest,
    request: Request,
    response: Response,
    use_case: FromDishka[RegisterUserUseCase],
    cookies: FromDishka[SessionCookiePolicy],
) -> UserResponse:
    try:
        result = await use_case.execute(
            body.email, body.password.get_secret_value(), _client_ip(request)
        )
    except TooManyAttemptsError as exc:
        raise _too_many(exc) from exc
    except EmailAlreadyRegisteredError as exc:
        # Известный долг: 409 позволяет перебором узнать, зарегистрирован ли email
        # (ARCHITECTURE.md, 11b). Rate limit делает перебор дорогим, но не невозможным.
        raise HTTPException(status.HTTP_409_CONFLICT, "Email уже зарегистрирован") from exc
    except (WeakPasswordError, InvalidEmailError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    cookies.set(response, result.session_token)
    return UserResponse.from_dto(result.user)


@router.post("/login")
@inject
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    use_case: FromDishka[LoginUseCase],
    cookies: FromDishka[SessionCookiePolicy],
) -> UserResponse:
    try:
        result = await use_case.execute(
            body.email, body.password.get_secret_value(), _client_ip(request)
        )
    except TooManyAttemptsError as exc:
        raise _too_many(exc) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль") from exc
    cookies.set(response, result.session_token)
    return UserResponse.from_dto(result.user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def logout(
    request: Request,
    response: Response,
    use_case: FromDishka[LogoutUseCase],
    cookies: FromDishka[SessionCookiePolicy],
) -> None:
    token = cookies.read(request)
    if token:
        await use_case.execute(token)
    cookies.clear(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def logout_all(
    user_id: CurrentUserId,
    response: Response,
    use_case: FromDishka[LogoutAllUseCase],
    cookies: FromDishka[SessionCookiePolicy],
) -> None:
    await use_case.execute(user_id)
    cookies.clear(response)


@router.get("/me")
@inject
async def me(user_id: CurrentUserId, use_case: FromDishka[GetCurrentUserUseCase]) -> UserResponse:
    dto = await use_case.execute(user_id)
    if dto is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
    return UserResponse.from_dto(dto)
