"""Сессия расширения: присутствие площадок и сигнал «расширение готово», по которому
продолжаются переносы, ждущие браузер."""

from uuid import uuid4

from syncplaylists.modules.extension.application.ports import PlatformPresence
from syncplaylists.modules.extension.application.session import DeviceSession
from syncplaylists.modules.extension.domain.value_objects import SessionState
from syncplaylists.shared_kernel.domain.value_objects import Platform
from tests.fakes import FakeTaskQueue
from tests.fakes.extension import RecordingHub


def _session() -> tuple[DeviceSession, RecordingHub, FakeTaskQueue]:
    hub, queue = RecordingHub(), FakeTaskQueue()
    session = DeviceSession(hub, queue, user_id=uuid4(), device_id=uuid4(), presence_ttl_seconds=60)
    return session, hub, queue


def _state(session: SessionState, external_user_id: str | None = "sc-1") -> PlatformPresence:
    return PlatformPresence(Platform.SOUNDCLOUD, session, external_user_id)


async def test_start_requeues_unfinished_tasks() -> None:
    session, hub, _ = _session()

    await session.start()

    assert hub.requeued == [session.device_id]


async def test_ready_platform_resumes_waiting_transfers_once() -> None:
    session, hub, queue = _session()

    await session.report(_state(SessionState.OK))
    await session.report(_state(SessionState.OK))  # то же состояние — без нового сигнала

    assert queue.enqueued == [
        ("resume_client_transfers", (session.user_id, Platform.SOUNDCLOUD.value))
    ]
    assert len(hub.presence) == 2


async def test_captcha_solved_resumes_again() -> None:
    session, _, queue = _session()

    await session.report(_state(SessionState.OK))
    await session.report(_state(SessionState.CAPTCHA))
    await session.report(_state(SessionState.OK))

    assert len(queue.enqueued) == 2


async def test_not_ready_platform_does_not_resume() -> None:
    session, _, queue = _session()

    await session.report(_state(SessionState.LOGGED_OUT, None))
    await session.report(_state(SessionState.NO_PERMISSION, None))

    assert queue.enqueued == []


async def test_heartbeat_republishes_all_platforms_and_close_drops_them() -> None:
    session, hub, _ = _session()
    await session.report(_state(SessionState.OK))
    await session.report(PlatformPresence(Platform.YANDEX, SessionState.LOGGED_OUT))

    await session.heartbeat()
    await session.close()

    assert {p.platform for p in hub.presence[-1][2]} == {Platform.SOUNDCLOUD, Platform.YANDEX}
    assert hub.dropped == [
        (session.user_id, session.device_id, [Platform.SOUNDCLOUD, Platform.YANDEX])
    ]
