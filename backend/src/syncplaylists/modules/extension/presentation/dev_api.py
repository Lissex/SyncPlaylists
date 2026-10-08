"""Dev-инструменты расширения (этап 4c-2). Подключаются в create_app только по флагам
настроек; при env=prod флаги запрещены (Settings падает на старте).

- dev_page_router — временная страница подтверждения кода привязки (/extension/pair),
  пока нет фронтенда (этап 5): вход, ввод кода, список устройств, отзыв, проверка связи;
- diagnostics_router — тестовая операция diagnostics.echo: проверка всего пути сервер →
  расширение → фоновая вкладка → сервер без площадок."""

from importlib.resources import files
from typing import Final, Literal
from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from syncplaylists.modules.extension.application.diagnostics import (
    DIAGNOSTICS_PAGE_PATH,
    DIAGNOSTICS_PAGE_TITLE,
    GetProbeUseCase,
    StartProbeUseCase,
)
from syncplaylists.modules.extension.domain.errors import DeviceNotFoundError
from syncplaylists.modules.identity.presentation.dependencies import CurrentUserId

_STATIC = files(__package__) / "static"
# Страница — только свой origin: скрипт отдельным файлом, без inline-скриптов и чужих
# ресурсов (inline-стили допустимы — это dev-страница без пользовательского HTML).
_PAGE_CSP: Final = (
    "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

dev_page_router = APIRouter(prefix="/extension", tags=["extension-dev"])
diagnostics_router = APIRouter(prefix="/extension", tags=["extension-dev"])


@dev_page_router.get("/pair", response_class=HTMLResponse, include_in_schema=False)
async def pair_page() -> HTMLResponse:
    return HTMLResponse(
        (_STATIC / "pair.html").read_text(encoding="utf-8"),
        headers={"Content-Security-Policy": _PAGE_CSP, "Cache-Control": "no-store"},
    )


@dev_page_router.get("/pair.js", include_in_schema=False)
async def pair_script() -> Response:
    return Response(
        (_STATIC / "pair.js").read_text(encoding="utf-8"),
        media_type="text/javascript; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


class ProbeStarted(BaseModel):
    probe_id: str


class ProbeStatus(BaseModel):
    status: Literal["pending", "ok", "error"]
    data: dict[str, str] | None = None
    error: str | None = None


@diagnostics_router.post("/devices/{device_id}/probe", status_code=status.HTTP_202_ACCEPTED)
@inject
async def start_probe(
    device_id: UUID, user_id: CurrentUserId, use_case: FromDishka[StartProbeUseCase]
) -> ProbeStarted:
    try:
        probe_id = await use_case.execute(user_id, device_id)
    except DeviceNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Устройство не найдено") from exc
    return ProbeStarted(probe_id=probe_id)


@diagnostics_router.get("/probes/{probe_id}")
@inject
async def get_probe(
    probe_id: str, user_id: CurrentUserId, use_case: FromDishka[GetProbeUseCase]
) -> ProbeStatus:
    record = await use_case.execute(user_id, probe_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Проверка не найдена или истекла")
    data = {key: str(value) for key, value in record.data.items()} if record.data else None
    return ProbeStatus(status=record.status, data=data, error=record.error)


# Страница, которую расширение открывает в фоновой вкладке для diagnostics.echo. Своя
# страница на origin API: обязательный host permission расширения уже на него есть.
@diagnostics_router.get(
    DIAGNOSTICS_PAGE_PATH.removeprefix("/extension"),
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def diagnostics_page() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=ru><head><meta charset=utf-8>"
        f"<title>{DIAGNOSTICS_PAGE_TITLE}</title></head>"
        "<body><p>SyncPlaylists: проверка связи с расширением. Вкладку закроет само "
        "расширение.</p></body></html>",
        headers={"Content-Security-Policy": "default-src 'none'", "Cache-Control": "no-store"},
    )
