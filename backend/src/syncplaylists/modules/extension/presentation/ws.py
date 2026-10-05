"""WebSocket расширения: `/extension/ws`.

Протокол (JSON-сообщения с полем type):
- расширение → сервер: hello (первым, с токеном устройства — не в URL), ping (раз в
  heartbeat_seconds), platform_state, connect_platform, result, progress;
- сервер → расширение: welcome, pong, task, platform_connected, error.

Задачи расширению выдаёт отдельная петля (`_pump_tasks`), сообщения читает основная.
Связь оборвалась (браузер закрыт) — присутствие снимается, ждущие воркеры сразу ставят
переносы на паузу (PAUSED_CLIENT); выданные, но не завершённые задачи вернутся в
очередь при следующем подключении."""

import asyncio
import contextlib
import logging
from typing import Any, Final

from dishka import AsyncContainer
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from syncplaylists.modules.extension.application.dto import DeviceDto
from syncplaylists.modules.extension.application.ports import ExtensionHub, PlatformPresence
from syncplaylists.modules.extension.application.session import DeviceSession
from syncplaylists.modules.extension.application.use_cases import (
    AuthenticateDeviceUseCase,
    ConnectPlatformViaExtensionUseCase,
)
from syncplaylists.modules.extension.domain.errors import PlatformAccountConflictError
from syncplaylists.modules.extension.presentation.config import ExtensionEndpointConfig
from syncplaylists.modules.extension.presentation.schemas import (
    ClientMessage,
    ConnectPlatformMessage,
    HelloMessage,
    PingMessage,
    PlatformStateMessage,
    ProgressMessage,
    ResultMessage,
)
from syncplaylists.shared_kernel.application.ports import TaskQueue

logger = logging.getLogger(__name__)

ws_router = APIRouter(prefix="/extension")

# Коды закрытия (4000–4999 — свои у приложения).
CLOSE_BAD_ORIGIN: Final = 4403
CLOSE_UNAUTHORIZED: Final = 4401
CLOSE_BAD_REQUEST: Final = 4400

_client_message: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
_hello: TypeAdapter[HelloMessage] = TypeAdapter(HelloMessage)


async def _authenticate(
    container: AsyncContainer, token: str, *, touch: bool = False, version: str | None = None
) -> DeviceDto | None:
    async with container() as request_container:
        use_case = await request_container.get(AuthenticateDeviceUseCase)
        return await use_case.execute(token, touch=touch, version=version)


@ws_router.websocket("/ws")
async def extension_ws(websocket: WebSocket) -> None:
    container: AsyncContainer = websocket.app.state.dishka_container
    config = await container.get(ExtensionEndpointConfig)
    if not config.origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=CLOSE_BAD_ORIGIN)
        return
    await websocket.accept()

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), config.hello_timeout_seconds)
        hello = _hello.validate_json(raw)
    except (TimeoutError, ValidationError, KeyError):
        await websocket.close(code=CLOSE_BAD_REQUEST)
        return
    except WebSocketDisconnect:
        return
    device = await _authenticate(container, hello.token, touch=True, version=hello.version)
    if device is None:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    session = DeviceSession(
        await container.get(ExtensionHub),
        await container.get(TaskQueue),
        user_id=device.user_id,
        device_id=device.id,
        presence_ttl_seconds=config.presence_ttl_seconds,
    )
    connection = _Connection(websocket, container, config, session, hello.token)
    await session.start()
    await connection.send(
        {
            "type": "welcome",
            "device_id": str(device.id),
            "heartbeat_seconds": config.heartbeat_seconds,
        }
    )
    logger.info("Расширение %s подключено", device.id)
    pump = asyncio.create_task(connection.pump_tasks())
    try:
        await connection.receive_loop()
    finally:
        pump.cancel()
        # Петля задач могла уже упасть на отправке в закрытый сокет — это не ошибка.
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump
        await session.close()
        logger.info("Расширение %s отключено", device.id)


class _Connection:
    def __init__(
        self,
        websocket: WebSocket,
        container: AsyncContainer,
        config: ExtensionEndpointConfig,
        session: DeviceSession,
        token: str,
    ) -> None:
        self._ws = websocket
        self._container = container
        self._config = config
        self._session = session
        self._token = token
        self._send_lock = asyncio.Lock()

    async def send(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            await self._ws.send_json(message)

    async def pump_tasks(self) -> None:
        while True:
            task = await self._session.next_task(self._config.heartbeat_seconds)
            if task is None:
                continue
            await self.send(
                {
                    "type": "task",
                    "task_id": task.task_id,
                    "op": task.op,
                    "args": dict(task.args),
                    "deadline": task.deadline,
                    "idempotency_key": task.idempotency_key,
                }
            )

    async def receive_loop(self) -> None:
        # Тишина дольше трёх heartbeat — расширение пропало, не дожидаясь TCP-таймаута.
        idle_timeout = self._config.heartbeat_seconds * 3
        while True:
            try:
                raw = await asyncio.wait_for(self._ws.receive_text(), idle_timeout)
            except (TimeoutError, WebSocketDisconnect):
                return
            except KeyError:  # бинарное сообщение — протокол только текстовый (JSON)
                await self.send({"type": "error", "code": "bad_message"})
                continue
            if len(raw) > self._config.max_message_bytes:
                await self.send({"type": "error", "code": "message_too_large"})
                continue
            try:
                message = _client_message.validate_json(raw)
            except ValidationError:
                await self.send({"type": "error", "code": "bad_message"})
                continue
            try:
                keep_open = await self._handle(message)
            except Exception:
                # Сбой обработки одного сообщения (БД, Redis) не рвёт соединение: расширение
                # получит error, задача — повтор по таймауту. Текст ошибки наружу не отдаём.
                logger.exception("Ошибка обработки сообщения %s расширения", message.type)
                await self.send({"type": "error", "code": "internal"})
                continue
            if not keep_open:
                await self._ws.close(code=CLOSE_UNAUTHORIZED)
                return

    async def _handle(self, message: ClientMessage) -> bool:
        """False — устройство отозвано, соединение закрывается."""
        if isinstance(message, PingMessage):
            # Отзыв устройства с сайта действует не позже следующего ping.
            if await _authenticate(self._container, self._token) is None:
                return False
            await self._session.heartbeat()
            await self.send({"type": "pong"})
        elif isinstance(message, PlatformStateMessage):
            await self._session.report(
                PlatformPresence(
                    platform=message.platform,
                    session=message.session,
                    external_user_id=message.external_user_id,
                )
            )
        elif isinstance(message, ConnectPlatformMessage):
            await self._connect_platform(message)
        elif isinstance(message, ResultMessage):
            outcome: dict[str, Any] = {"ok": message.ok}
            if message.data is not None:
                outcome["data"] = message.data
            if message.error is not None:
                outcome["error"] = message.error.model_dump()
            if not await self._session.complete(message.task_id, outcome):
                logger.info("Результат неизвестной/чужой задачи %s отброшен", message.task_id)
        elif isinstance(message, ProgressMessage):
            await self._session.progress(message.task_id)
        return True

    async def _connect_platform(self, message: ConnectPlatformMessage) -> None:
        try:
            async with self._container() as request_container:
                use_case = await request_container.get(ConnectPlatformViaExtensionUseCase)
                account = await use_case.execute(
                    self._session.user_id,
                    message.platform,
                    message.external_user_id,
                    message.display_name,
                )
        except PlatformAccountConflictError:
            await self.send(
                {
                    "type": "error",
                    "request_id": message.request_id,
                    "code": "account_already_connected",
                    "message": "На этой площадке подключён другой аккаунт — сначала отключите его",
                }
            )
            return
        await self.send(
            {
                "type": "platform_connected",
                "request_id": message.request_id,
                "platform": message.platform.value,
                "account_id": str(account.id),
            }
        )
