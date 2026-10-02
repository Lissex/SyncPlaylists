from typing import Any

from arq.connections import RedisSettings as ArqRedisSettings
from dishka.integrations.arq import setup_dishka

from syncplaylists.bootstrap.container import make_container
from syncplaylists.infrastructure.config.settings import Settings

settings = Settings()


async def ping(ctx: dict[str, Any]) -> str:
    return "pong"


class WorkerSettings:
    functions = (ping,)
    redis_settings = ArqRedisSettings.from_dsn(str(settings.redis.dsn))


setup_dishka(make_container(settings), WorkerSettings)
