from collections.abc import AsyncIterator

from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.infrastructure.db.engine import create_engine
from syncplaylists.infrastructure.db.session import create_session_factory


class SettingsProvider(Provider):
    scope = Scope.APP

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

    @provide
    def get_settings(self) -> Settings:
        return self._settings


class DatabaseProvider(Provider):
    scope = Scope.APP

    @provide
    async def get_engine(self, settings: Settings) -> AsyncIterator[AsyncEngine]:
        engine = create_engine(settings.db)
        yield engine
        await engine.dispose()

    @provide
    def get_session_factory(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return create_session_factory(engine)

    @provide(scope=Scope.REQUEST)
    async def get_session(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


def make_container(settings: Settings) -> AsyncContainer:
    return make_async_container(SettingsProvider(settings), DatabaseProvider())
