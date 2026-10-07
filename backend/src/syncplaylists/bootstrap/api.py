from dishka.integrations.fastapi import FromDishka, inject, setup_dishka
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.bootstrap.container import make_container
from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.modules.accounts.presentation.api import router as accounts_router
from syncplaylists.modules.extension.presentation.api import router as extension_router
from syncplaylists.modules.extension.presentation.dev_api import (
    dev_page_router,
    diagnostics_router,
)
from syncplaylists.modules.extension.presentation.ws import ws_router as extension_ws_router
from syncplaylists.modules.identity.presentation.api import router as auth_router
from syncplaylists.modules.transfers.presentation.api import links_router
from syncplaylists.modules.transfers.presentation.api import router as transfers_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="SyncPlaylists")

    # Сессия — cookie, поэтому allow_credentials=True и строгий allowlist origin'ов
    # (никогда не "*" — валидатор CorsSettings это запрещает).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/db")
    @inject
    async def health_db(session: FromDishka[AsyncSession]) -> dict[str, str]:
        await session.execute(text("SELECT 1"))
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(accounts_router)
    app.include_router(transfers_router)
    app.include_router(links_router)
    app.include_router(extension_router)
    app.include_router(extension_ws_router)
    # Dev-инструменты расширения (этап 4c-2); при env=prod Settings их не пропустит.
    if settings.extension.dev_page_enabled:
        app.include_router(dev_page_router)
    if settings.extension.diagnostics_enabled:
        app.include_router(diagnostics_router)

    container = make_container(settings)
    setup_dishka(container, app)

    return app
