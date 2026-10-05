"""Канал задач «воркер → расширение» на Redis. Воркеры (ARQ) и WebSocket расширений
(процесс api) — разные процессы, поэтому всё общее состояние лежит в Redis:

    ext:presence:<user>:<platform>  HASH  device_id → {session, external_user_id, seen}
    ext:task:<task_id>              HASH  кому, что, дедлайн, ключ идемпотентности (сутки)
    ext:q:<device_id>               LIST  task_id в очереди устройства
    ext:inflight:<device_id>        LIST  task_id, выданные устройству и ещё не завершённые
    ext:res:<task_id>               LIST  результат для ждущего воркера (5 минут)
    ext:done:<user>:<key>           STR   результат записи по ключу идемпотентности (сутки)

Ключи только с id и хэшами — ни токенов, ни cookie здесь нет и быть не может."""

import json
import math
import time
from collections.abc import Mapping, Sequence
from typing import Any, Final
from uuid import UUID, uuid4

from redis.asyncio import Redis

from syncplaylists.modules.extension.application.operations import (
    OPERATIONS,
    OperationSpec,
    error_from_wire,
    wire_op,
)
from syncplaylists.modules.extension.application.ports import (
    DeliveredTask,
    ExtensionCall,
    PlatformPresence,
)
from syncplaylists.modules.extension.domain.value_objects import SessionState
from syncplaylists.shared_kernel.domain.errors import (
    ExtensionUnavailableError,
    ExtensionUnavailableReason,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform

_TASK_TTL: Final = 86400
_DONE_TTL: Final = 86400
_RESULT_TTL: Final = 300
# Как часто ждущий воркер перепроверяет дедлайн (его двигает progress) и что устройство
# всё ещё на связи.
_POLL_SECONDS: Final = 1.0

# Если в браузере нет готовой площадки, причина паузы — самая «близкая к готовности»:
# пользователю важнее узнать про капчу, чем про то, что где-то ещё нет разрешения.
_REASON_PRIORITY: Final = (
    (SessionState.CAPTCHA, ExtensionUnavailableReason.CAPTCHA),
    (SessionState.LOGGED_OUT, ExtensionUnavailableReason.LOGGED_OUT),
    (SessionState.NO_PERMISSION, ExtensionUnavailableReason.NO_PERMISSION),
)


def _text(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else value


def _fields(raw: Mapping[Any, Any]) -> dict[str, str]:
    return {str(_text(k)): str(_text(v)) for k, v in raw.items()}


def _presence_key(user_id: UUID, platform: Platform) -> str:
    return f"ext:presence:{user_id}:{platform.value}"


def _done_key(user_id: UUID | str, key: str) -> str:
    return f"ext:done:{user_id}:{key}"


class RedisExtensionChannel:
    """ExtensionChannel (сторона воркера) и ExtensionHub (сторона WebSocket) — одна
    реализация, потому что обе стороны работают с одними и теми же ключами."""

    def __init__(
        self,
        redis: Redis,
        *,
        presence_ttl_seconds: int,
        operations: Mapping[str, OperationSpec] = OPERATIONS,
    ) -> None:
        self._redis = redis
        self._presence_ttl = presence_ttl_seconds
        # Реестр операций с таймаутами; подменяется только в тестах (короткие таймауты).
        self._operations = operations

    # ------------------------------------------------------------------ воркер

    async def call(self, call: ExtensionCall) -> Mapping[str, Any]:
        spec = self._operations.get(call.operation)
        if spec is None:
            raise ValueError(f"Неизвестная операция расширения: {call.operation}")
        if call.idempotency_key is not None:
            done = await self._redis.get(_done_key(call.user_id, call.idempotency_key))
            if done is not None:
                # Операция уже выполнена (ответ пришёл после того, как мы перестали
                # ждать) — второй раз в браузер не отправляем.
                return dict(json.loads(done))

        device_id = await self._pick_device(call)
        task_id = uuid4().hex
        timeout = spec.timeout_for(call.items)
        task = {
            "device": str(device_id),
            "user": str(call.user_id),
            "platform": call.platform.value,
            "op": wire_op(call.platform, call.operation),
            "args": json.dumps(dict(call.args)),
            "deadline": str(time.time() + timeout),
            "extend": str(spec.timeout_seconds),
            "key": call.idempotency_key or "",
        }
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(f"ext:task:{task_id}", mapping=task)
            pipe.expire(f"ext:task:{task_id}", _TASK_TTL)
            pipe.rpush(f"ext:q:{device_id}", task_id)
            pipe.expire(f"ext:q:{device_id}", _TASK_TTL)
            await pipe.execute()

        outcome = await self._wait(call, device_id, task_id)
        if outcome.get("ok"):
            data = outcome.get("data")
            return dict(data) if isinstance(data, Mapping) else {}
        error = outcome.get("error")
        raise error_from_wire(call.platform, error if isinstance(error, Mapping) else {})

    async def _pick_device(self, call: ExtensionCall) -> UUID:
        raw = await self._redis.hgetall(_presence_key(call.user_id, call.platform))  # type: ignore[misc]
        fresh_after = time.time() - self._presence_ttl
        sessions: set[SessionState] = set()
        mismatch = False
        for device, value in _fields(raw).items():
            entry = json.loads(value)
            if float(entry.get("seen", 0)) < fresh_after:
                continue
            session = SessionState(entry["session"])
            if session is SessionState.OK:
                if entry.get("external_user_id") == call.external_user_id:
                    return UUID(device)
                mismatch = True
            sessions.add(session)
        if mismatch:
            raise ExtensionUnavailableError(
                call.platform, ExtensionUnavailableReason.SESSION_MISMATCH
            )
        for state, reason in _REASON_PRIORITY:
            if state in sessions:
                raise ExtensionUnavailableError(call.platform, reason)
        raise ExtensionUnavailableError(call.platform, ExtensionUnavailableReason.OFFLINE)

    async def _wait(self, call: ExtensionCall, device_id: UUID, task_id: str) -> dict[str, Any]:
        result_key = f"ext:res:{task_id}"
        while True:
            deadline_raw = await self._redis.hget(f"ext:task:{task_id}", "deadline")  # type: ignore[misc]
            remaining = float(_text(deadline_raw) or 0) - time.time()
            if remaining <= 0:
                await self._abandon(device_id, task_id)
                raise ExtensionUnavailableError(call.platform, ExtensionUnavailableReason.TIMEOUT)
            wait = max(1, math.ceil(min(remaining, _POLL_SECONDS)))
            popped = await self._redis.blpop([result_key], timeout=wait)  # type: ignore[misc]
            if popped is not None:
                return dict(json.loads(popped[1]))
            if not await self._is_present(call.user_id, call.platform, device_id):
                # Браузер закрыли посреди задачи: не ждём таймаута, перенос встанет на
                # паузу сразу. Если задача всё же выполнится, её результат по ключу
                # идемпотентности сохранит complete().
                await self._abandon(device_id, task_id)
                raise ExtensionUnavailableError(call.platform, ExtensionUnavailableReason.OFFLINE)

    async def _abandon(self, device_id: UUID, task_id: str) -> None:
        # Ещё не выданную задачу не выдаём; выданную расширение может доделать — её
        # результат по ключу идемпотентности сохранится.
        await self._redis.lrem(f"ext:q:{device_id}", 0, task_id)  # type: ignore[misc]

    async def _is_present(self, user_id: UUID, platform: Platform, device_id: UUID) -> bool:
        value = await self._redis.hget(_presence_key(user_id, platform), str(device_id))  # type: ignore[misc]
        if value is None:
            return False
        entry = json.loads(value)
        return float(entry.get("seen", 0)) >= time.time() - self._presence_ttl

    # ------------------------------------------------------------------ WebSocket

    async def publish_presence(
        self,
        user_id: UUID,
        device_id: UUID,
        presence: Sequence[PlatformPresence],
        ttl_seconds: int,
    ) -> None:
        now = time.time()
        async with self._redis.pipeline(transaction=True) as pipe:
            for item in presence:
                key = _presence_key(user_id, item.platform)
                entry = {
                    "session": item.session.value,
                    "external_user_id": item.external_user_id,
                    "seen": now,
                }
                pipe.hset(key, str(device_id), json.dumps(entry))
                pipe.expire(key, ttl_seconds)
            await pipe.execute()

    async def drop_presence(
        self, user_id: UUID, device_id: UUID, platforms: Sequence[Platform]
    ) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            for platform in platforms:
                pipe.hdel(_presence_key(user_id, platform), str(device_id))
            await pipe.execute()

    async def requeue_inflight(self, device_id: UUID) -> None:
        inflight, queue = f"ext:inflight:{device_id}", f"ext:q:{device_id}"
        # С конца inflight в начало очереди — исходный порядок сохраняется.
        while await self._redis.lmove(inflight, queue, "RIGHT", "LEFT") is not None:
            pass

    async def next_task(self, device_id: UUID, wait_seconds: float) -> DeliveredTask | None:
        inflight, queue = f"ext:inflight:{device_id}", f"ext:q:{device_id}"
        until = time.time() + wait_seconds
        while (remaining := until - time.time()) > 0:
            moved = await self._redis.blmove(
                queue, inflight, max(1, math.ceil(remaining)), "LEFT", "RIGHT"
            )
            task_id = _text(moved)
            if task_id is None:
                return None
            task = _fields(await self._redis.hgetall(f"ext:task:{task_id}"))  # type: ignore[misc]
            if not task or float(task.get("deadline", 0)) < time.time():
                # Ответ уже никто не ждёт — в браузер не отправляем.
                await self._redis.lrem(inflight, 0, task_id)  # type: ignore[misc]
                continue
            return DeliveredTask(
                task_id=task_id,
                op=task["op"],
                args=json.loads(task["args"]),
                deadline=float(task["deadline"]),
                idempotency_key=task.get("key") or None,
            )
        return None

    async def complete(self, device_id: UUID, task_id: str, outcome: Mapping[str, Any]) -> bool:
        task = _fields(await self._redis.hgetall(f"ext:task:{task_id}"))  # type: ignore[misc]
        if not task or task.get("device") != str(device_id):
            return False
        async with self._redis.pipeline(transaction=True) as pipe:
            data = outcome.get("data")
            if outcome.get("ok") and task.get("key") and isinstance(data, Mapping):
                pipe.set(_done_key(task["user"], task["key"]), json.dumps(dict(data)), ex=_DONE_TTL)
            pipe.lpush(f"ext:res:{task_id}", json.dumps(dict(outcome)))
            pipe.expire(f"ext:res:{task_id}", _RESULT_TTL)
            pipe.lrem(f"ext:inflight:{device_id}", 0, task_id)
            # Результат дослан после переподключения, а задачу уже вернули в очередь
            # (requeue_inflight) — второй раз её не выдаём.
            pipe.lrem(f"ext:q:{device_id}", 0, task_id)
            await pipe.execute()
        return True

    async def extend(self, device_id: UUID, task_id: str) -> None:
        key = f"ext:task:{task_id}"
        task = _fields(await self._redis.hgetall(key))  # type: ignore[misc]
        if not task or task.get("device") != str(device_id):
            return
        deadline = max(float(task["deadline"]), time.time() + float(task["extend"]))
        await self._redis.hset(key, "deadline", str(deadline))  # type: ignore[misc]
