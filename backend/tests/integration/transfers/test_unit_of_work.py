import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.infrastructure.events.redis_publisher import RedisEventPublisher
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import ExistingPlaylist, PlaylistSource
from syncplaylists.modules.transfers.infrastructure.uow import SqlUnitOfWork
from syncplaylists.shared_kernel.domain.value_objects import Platform, PlaylistRef


async def test_commit_publishes_domain_events_to_redis(
    session: AsyncSession, redis_url: str
) -> None:
    redis: Redis = Redis.from_url(redis_url)
    try:
        transfer = Transfer(
            id=uuid4(),
            user_id=uuid4(),
            source=PlaylistSource(ref=PlaylistRef(Platform.VK, "src")),
            destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, "dst")),
        )
        channel = f"transfer:{transfer.id}"
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            # redis-py доставляет подписное подтверждение первым сообщением — съедаем его.
            await pubsub.get_message(timeout=2)

            uow = SqlUnitOfWork(session, RedisEventPublisher(redis))
            async with uow:
                transfer.start(datetime.now(UTC))
                uow.track(transfer)
                await uow.commit()

            message = await asyncio.wait_for(_next_message(pubsub), timeout=5)
            payload = json.loads(message["data"])
            assert payload["type"] == "TransferStarted"
            assert payload["transfer_id"] == str(transfer.id)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
    finally:
        await redis.aclose()


async def _next_message(pubsub: Any) -> dict[str, Any]:
    while True:
        message = await pubsub.get_message(timeout=5)
        if message is not None and message["type"] == "message":
            return message
