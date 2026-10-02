from dishka.integrations.fastapi import FromDishka, inject, setup_dishka
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.bootstrap.container import make_container
from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.modules.transfers.presentation.api import router as transfers_router


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="SyncPlaylists")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/db")
    @inject
    async def health_db(session: FromDishka[AsyncSession]) -> dict[str, str]:
        await session.execute(text("SELECT 1"))
        return {"status": "ok"}

    app.include_router(transfers_router)

    container = make_container(settings or Settings())
    setup_dishka(container, app)

    return app
