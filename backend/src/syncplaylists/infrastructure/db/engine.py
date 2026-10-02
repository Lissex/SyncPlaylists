from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from syncplaylists.infrastructure.config.settings import DatabaseSettings


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    return create_async_engine(
        str(settings.dsn),
        pool_size=settings.pool_size,
        echo=settings.echo,
    )
