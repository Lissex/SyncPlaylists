from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Depends, HTTPException, Request, status

from syncplaylists.modules.identity.application.use_cases import ResolveSessionUseCase
from syncplaylists.modules.identity.presentation.cookies import SessionCookiePolicy


@inject
async def get_current_user_id(
    request: Request,
    cookies: FromDishka[SessionCookiePolicy],
    resolve_session: FromDishka[ResolveSessionUseCase],
) -> UUID:
    token = cookies.read(request)
    user_id = await resolve_session.execute(token) if token else None
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход")
    return user_id


# Публичная точка identity для presentation других контекстов (transfers, accounts):
# `user_id: CurrentUserId` в сигнатуре эндпоинта.
CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]
