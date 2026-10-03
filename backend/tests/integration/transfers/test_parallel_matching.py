"""Много run_match одного переноса параллельно: счётчики transfers сходятся с реальными
статусами items, переход RUNNING → REVIEW/WRITING происходит ровно один раз, общий кэш
track_matches не падает на одновременной записи одного и того же соответствия."""

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from dishka import AsyncContainer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syncplaylists.bootstrap.container import make_container
from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.integrations.platforms.fake.catalog import DEMO_TRACKS
from syncplaylists.modules.accounts.application.use_cases import ConnectAccountUseCase
from syncplaylists.modules.catalog.application.use_cases import EnsurePlatformTrackUseCase
from syncplaylists.modules.identity.infrastructure.orm import UserOrm
from syncplaylists.modules.transfers.application.ports import TransferRepository
from syncplaylists.modules.transfers.application.use_cases import MatchTransferItemUseCase
from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import (
    ExistingPlaylist,
    PlaylistSource,
    TransferItemStatus,
    TransferProgress,
    TransferStatus,
)
from syncplaylists.modules.transfers.infrastructure.orm import TransferItemOrm, TransferOrm
from syncplaylists.shared_kernel.application.ports import PlatformCredentials
from syncplaylists.shared_kernel.domain.value_objects import (
    ISRC,
    Duration,
    ExternalTrackRef,
    Platform,
    PlaylistRef,
    Transport,
)

_ITEMS = 30


async def _create_user_with_accounts(container: AsyncContainer) -> UUID:
    user_id = uuid4()
    async with container() as request:
        session = await request.get(AsyncSession)
        session.add(
            UserOrm(
                id=user_id,
                email=f"{user_id.hex}@example.com",
                password_hash="x",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
        connect = await request.get(ConnectAccountUseCase)
        for platform in (Platform.VK, Platform.SPOTIFY):
            await connect.execute(
                user_id=user_id,
                platform=platform,
                transport=Transport.UNOFFICIAL,
                external_user_id=f"{platform.value}-parallel",
                display_name=None,
                credentials=PlatformCredentials(access_token="token"),
            )
    return user_id


async def _create_running_transfer(
    container: AsyncContainer, user_id: UUID, unmatched_every: int | None
) -> Transfer:
    # Уникальные source-треки на прогон (кэш track_matches пуст), но каждый повторяется
    # в плейлисте ~10 раз: десяток джоб одновременно пишет ОДНО соответствие в кэш.
    run = uuid4().hex[:8]
    transfer = Transfer(
        id=uuid4(),
        user_id=user_id,
        source=PlaylistSource(ref=PlaylistRef(Platform.VK, f"parallel-{run}")),
        destination=ExistingPlaylist(ref=PlaylistRef(Platform.SPOTIFY, f"parallel-{run}")),
    )
    transfer.start(datetime.now(UTC))
    async with container() as request:
        session = await request.get(AsyncSession)
        ensure = await request.get(EnsurePlatformTrackUseCase)
        transfers = await request.get(TransferRepository)
        for position in range(_ITEMS):
            if unmatched_every is not None and position % unmatched_every == 0:
                ref = ExternalTrackRef(Platform.VK, f"{run}-unknown-{position}")
                await ensure.execute(ref, f"Unknown song {run} {position}", "Nobody")
            else:
                demo = DEMO_TRACKS[position % len(DEMO_TRACKS)]
                ref = ExternalTrackRef(Platform.VK, f"{run}-{demo.slug}")
                await ensure.execute(
                    ref, demo.title, demo.artist, Duration(demo.duration_ms), ISRC(demo.isrc)
                )
            transfer.add_item(position, ref)
        await transfers.save(transfer)
        await session.commit()
    return transfer


async def _match_all_in_parallel(container: AsyncContainer, transfer_id: UUID) -> None:
    async def match(position: int) -> None:
        async with container() as request:
            use_case = await request.get(MatchTransferItemUseCase)
            await use_case.execute(transfer_id, position)

    await asyncio.gather(*(match(position) for position in range(_ITEMS)))


async def _collect_events(redis: Redis, channel: str, stop: asyncio.Event) -> list[str]:
    received: list[str] = []
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        while not stop.is_set():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if message is not None:
                received.append(json.loads(message["data"])["type"])
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()  # type: ignore[no-untyped-call]
    return received


@pytest.mark.parametrize(
    ("unmatched_every", "expected_status"),
    [(None, TransferStatus.WRITING), (4, TransferStatus.REVIEW)],
)
async def test_parallel_run_match_keeps_counters_consistent(
    settings: Settings,
    redis_url: str,
    unmatched_every: int | None,
    expected_status: TransferStatus,
) -> None:
    container = make_container(settings)
    redis: Redis = Redis.from_url(redis_url)
    try:
        user_id = await _create_user_with_accounts(container)
        transfer = await _create_running_transfer(container, user_id, unmatched_every)

        stop = asyncio.Event()
        events_task = asyncio.create_task(_collect_events(redis, f"transfer:{transfer.id}", stop))
        await asyncio.sleep(0.2)  # подписка успела оформиться
        await _match_all_in_parallel(container, transfer.id)
        await asyncio.sleep(0.5)  # дочитать последние события
        stop.set()
        events = await events_task

        async with container() as request:
            session = await request.get(AsyncSession)
            row = await session.get(TransferOrm, transfer.id)
            statuses = [
                TransferItemStatus(status)
                for status in await session.scalars(
                    select(TransferItemOrm.status).where(TransferItemOrm.transfer_id == transfer.id)
                )
            ]
    finally:
        await redis.aclose()
        await container.close()

    assert row is not None
    actual = TransferProgress.from_statuses(statuses)
    stored = TransferProgress(
        total=row.total,
        pending=row.pending,
        matched=row.matched,
        uncertain=row.uncertain,
        not_found=row.not_found,
        added=row.added,
        failed=row.failed,
    )
    assert stored == actual  # ни одного потерянного инкремента
    assert actual.pending == 0
    assert actual.total == _ITEMS
    assert row.status == expected_status.value

    outcome_events = Counter(events)
    assert (
        outcome_events["TrackMatched"]
        + outcome_events["TrackNotFound"]
        + outcome_events["TrackNeedsReview"]
        == _ITEMS
    )
    expected_writing_events = 1 if expected_status is TransferStatus.WRITING else 0
    assert outcome_events["TransferWritingStarted"] == expected_writing_events
