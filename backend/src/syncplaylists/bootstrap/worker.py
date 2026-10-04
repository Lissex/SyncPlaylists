from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings as ArqRedisSettings
from arq.cron import CronJob
from dishka.integrations.arq import setup_dishka

from syncplaylists.bootstrap.container import make_container
from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.modules.transfers.presentation.tasks import (
    RATE_LIMITED_MAX_TRIES,
    resume_transfer,
    run_match,
    run_transfer,
    run_write,
    sweep_stale_transfers,
)

settings = Settings()


async def ping(ctx: dict[str, Any]) -> str:
    return "pong"


class WorkerSettings:
    # transfer/match/write — три логические очереди из этого этапа, пока один
    # физический процесс (как и docker-compose: один сервис `worker`); разные
    # concurrency/rate-limit по площадкам — задел на этап 4. См. ARCHITECTURE.md.
    functions = (ping, run_transfer, run_match, run_write, resume_transfer)
    # Каждые 5 минут — порог "застывания" в SweepStaleTransfersUseCase — 10 минут,
    # так что застывший перенос подхватится максимум через ~15 минут после сбоя.
    cron_jobs: ClassVar[list[CronJob]] = [cron(sweep_stale_transfers, minute=set(range(0, 60, 5)))]
    # ARQ обрывает задачу после max_tries (по умолчанию 5) — не меньше бюджета
    # повторов при rate limit, иначе трек навсегда останется PENDING.
    max_tries = RATE_LIMITED_MAX_TRIES
    redis_settings = ArqRedisSettings.from_dsn(str(settings.redis.dsn))


setup_dishka(make_container(settings), WorkerSettings)
