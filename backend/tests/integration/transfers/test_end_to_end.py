import asyncio
import json
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import uvicorn
from arq.connections import RedisSettings as ArqRedisSettings
from arq.worker import Worker, create_worker
from dishka.integrations.arq import setup_dishka
from httpx import ASGITransport, AsyncClient

from syncplaylists.bootstrap.api import create_app
from syncplaylists.bootstrap.container import make_container
from syncplaylists.infrastructure.config.settings import Settings
from syncplaylists.modules.transfers.presentation.tasks import run_match, run_transfer, run_write

if sys.platform == "win32" and not hasattr(signal, "SIGUSR1"):
    # arq.worker.Worker.close() безусловно дёргает signal.SIGUSR1 (POSIX-only) — на
    # Windows такого атрибута нет вовсе. По коду handle_sig он используется только как
    # значение для лога и для отмены уже пустых задач, так что подмена безопасна и
    # нужна только для прогона этого теста на Windows-хосте (в проде/CI — Linux).
    signal.SIGUSR1 = signal.SIGTERM  # type: ignore[attr-defined]


@asynccontextmanager
async def _run_app(settings: Settings) -> AsyncIterator[str]:
    # httpx.ASGITransport не годится здесь: он не отдаёт ответ клиенту, пока ASGI-вызов
    # не завершится целиком, а SSE-эндпоинт — бесконечный генератор (живая подписка на
    # Redis) и сам никогда не завершается. Для честной проверки потоковой отдачи нужен
    # настоящий сетевой сервер на loopback.
    app = create_app(settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.025)
        else:
            raise AssertionError("uvicorn не поднялся за отведённое время")
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


def _make_worker_settings_class() -> type:
    # Локальный класс на каждый вызов: setup_dishka мутирует его как атрибуты класса
    # (ctx/on_job_start/on_job_end) — общий класс на уровне модуля потёк бы состояние
    # между тестами, если бы их здесь было больше одного.
    class _WorkerSettings:
        functions = (run_transfer, run_match, run_write)

    return _WorkerSettings


async def _drain_queue(worker: Worker, max_rounds: int = 20) -> None:
    for _ in range(max_rounds):
        await worker.async_run()
        pending = await worker.pool.zcard(worker.queue_name)
        if pending == 0 and not worker.tasks:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("ARQ-воркер не разобрал очередь за отведённое число проходов")


async def _run_worker_until_idle(settings: Settings, redis_url: str) -> None:
    worker_container = make_container(settings)
    worker_settings_cls = _make_worker_settings_class()
    setup_dishka(worker_container, worker_settings_cls)
    worker = create_worker(
        worker_settings_cls,
        redis_settings=ArqRedisSettings.from_dsn(redis_url),
        burst=True,
        handle_signals=False,
        poll_delay=0.05,
    )
    try:
        await _drain_queue(worker)
    finally:
        await worker.close()
        await worker_container.close()


async def _login_with_accounts(client: AsyncClient) -> None:
    # С этапа 4a перенос — от имени вошедшего пользователя (cookie хранит AsyncClient)
    # и требует подключённых аккаунтов на площадках источника и назначения.
    register = await client.post(
        "/auth/register",
        json={"email": f"e2e-{uuid4().hex}@example.com", "password": "correct horse"},
    )
    assert register.status_code == 201, register.text
    for platform in ("vk", "spotify"):
        connect = await client.post(
            "/accounts",
            # external_user_id сервер берёт из профиля площадки (фейк выводит его из токена).
            json={"platform": platform, "access_token": f"{platform}-e2e-token"},
        )
        assert connect.status_code == 201, connect.text


_DEMO_TRANSFER = {
    "source": {"kind": "playlist", "platform": "vk", "external_id": "demo"},
    "destination": {"kind": "existing", "platform": "spotify", "external_id": "demo"},
}


async def _start_demo_transfer(client: AsyncClient) -> str:
    response = await client.post("/transfers", json=_DEMO_TRANSFER)
    assert response.status_code == 201, response.text
    transfer_id: str = response.json()["id"]
    return transfer_id


async def _assert_done(client: AsyncClient, transfer_id: str) -> None:
    body = (await client.get(f"/transfers/{transfer_id}")).json()
    assert body["status"] == "done", body
    assert len(body["items"]) == 3
    assert all(item["status"] == "added" for item in body["items"])


async def _collect_sse_events(
    client: AsyncClient, transfer_id: str, received: list[str], ready: asyncio.Event
) -> None:
    async with client.stream("GET", f"/transfers/{transfer_id}/events") as response:
        assert response.status_code == 200, await response.aread()
        current_event: str | None = None
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current_event = line.removeprefix("event:").strip()
            elif line.startswith("data:") and current_event is not None:
                raw = line.removeprefix("data:").strip()
                if not raw:
                    continue
                payload = json.loads(raw)
                if current_event == "snapshot":
                    ready.set()
                elif current_event == "domain_event":
                    received.append(payload["type"])
                # "progress" (счётчики + ETA раз в несколько секунд) здесь не нужен.


async def test_transfer_runs_end_to_end_and_streams_events_over_sse(
    settings: Settings, redis_url: str
) -> None:
    async with (
        _run_app(settings) as base_url,
        AsyncClient(base_url=base_url, timeout=10.0) as client,
    ):
        await _login_with_accounts(client)
        transfer_id = await _start_demo_transfer(client)

        received: list[str] = []
        snapshot_ready = asyncio.Event()
        sse_task = asyncio.create_task(
            _collect_sse_events(client, transfer_id, received, snapshot_ready)
        )
        await asyncio.wait_for(snapshot_ready.wait(), timeout=5)

        await _run_worker_until_idle(settings, redis_url)

        # Даём SSE-подписчику время дочитать последнее сообщение (TransferCompleted)
        # прежде чем его оборвать.
        await asyncio.sleep(0.5)
        sse_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sse_task

        get_response = await client.get(f"/transfers/{transfer_id}")
        body = get_response.json()

    assert body["status"] == "done"
    assert len(body["items"]) == 3
    assert all(item["status"] == "added" for item in body["items"])

    assert body["progress"]["status"] == "done"
    assert body["progress"]["added"] == 3
    assert body["progress"]["pending"] == 0
    assert body["progress"]["eta_seconds"] is None  # ETA только для running

    assert received[0] == "TransferStarted"
    assert received.count("TrackMatched") == 3
    assert "TransferWritingStarted" in received
    assert received[-1] == "TransferCompleted"


# Регрессия: track_matches — глобальный кэш. Раньше второй перенос тех же треков (или
# параллельный перенос другим пользователем) падал в run_match на UniqueViolation
# (source_pt_id, target_platform), и перенос навсегда застревал в RUNNING.


async def test_same_playlist_transferred_twice_in_a_row(settings: Settings, redis_url: str) -> None:
    app = create_app(settings)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await _login_with_accounts(client)

        first = await _start_demo_transfer(client)
        await _run_worker_until_idle(settings, redis_url)
        second = await _start_demo_transfer(client)
        await _run_worker_until_idle(settings, redis_url)

        await _assert_done(client, first)
        await _assert_done(client, second)


async def test_same_playlist_transferred_concurrently_by_two_users(
    settings: Settings, redis_url: str
) -> None:
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as alice,
        AsyncClient(transport=transport, base_url="http://testserver") as bob,
    ):
        await _login_with_accounts(alice)
        await _login_with_accounts(bob)
        alice_transfer = await _start_demo_transfer(alice)
        bob_transfer = await _start_demo_transfer(bob)

        # Один воркер, обе очереди разом: run_match обоих переносов по тем же трекам
        # идут параллельно (ARQ max_jobs > 1) и пишут в общий кэш track_matches.
        await _run_worker_until_idle(settings, redis_url)

        await _assert_done(alice, alice_transfer)
        await _assert_done(bob, bob_transfer)
