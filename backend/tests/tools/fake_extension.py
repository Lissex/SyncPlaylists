"""Фейковое браузерное расширение (этап 4c-1): площадка в памяти + то же поведение,
что у настоящего расширения, — реестр операций, журнал write-операций по ключу
идемпотентности, повтор задачи с тем же ключом не выполняется второй раз.

Используется:
- интеграционными тестами канала — `HubDevice` работает с Redis-хабом напрямую, без WS;
- сквозным тестом против запущенного приложения — `python -m tests.tools.fake_extension
  e2e --api http://localhost:8000` (см. scripts/e2e-extension.ps1).

Площадка — общий демо-каталог фейка (DEMO_TRACKS, те же ISRC): перенос «фейковый
Spotify → VK через расширение» находит все треки через IsrcStrategy."""

import argparse
import asyncio
import contextlib
import json
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from syncplaylists.integrations.platforms.fake.catalog import DEMO_TRACKS, DemoTrack
from syncplaylists.modules.extension.application.ports import (
    ExtensionHub,
    PlatformPresence,
)
from syncplaylists.modules.extension.domain.value_objects import SessionState
from syncplaylists.shared_kernel.domain.value_objects import Platform

_WRITE_OPS = {"create_playlist", "add_tracks", "add_to_library", "like", "set_playlist_tracks"}


class OpError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.message = message


@dataclass
class FakeBrowserPlatform:
    """Площадка «в браузере»: плейлисты и медиатека пользователя в памяти."""

    platform: Platform
    external_user_id: str = "fake-user"
    page_size: int = 2
    playlists: dict[str, list[str]] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    library: list[str] = field(default_factory=list)
    # Журнал write-операций по ключу идемпотентности (как storage.local расширения).
    journal: dict[str, dict[str, Any]] = field(default_factory=dict)
    executed: list[str] = field(default_factory=list)

    def _track(self, track: DemoTrack) -> dict[str, Any]:
        return {
            "id": f"{self.platform.value}-{track.slug}",
            "title": track.title,
            "artist": track.artist,
            "duration_ms": track.duration_ms,
            "isrc": track.isrc,
        }

    def _page(
        self, items: list[dict[str, Any]], cursor: str | None
    ) -> tuple[list[Any], str | None]:
        start = int(cursor or 0)
        end = start + self.page_size
        return items[start:end], (str(end) if end < len(items) else None)

    async def execute(
        self,
        op: str,
        args: Mapping[str, Any],
        idempotency_key: str | None,
        account: str | None = None,
    ) -> dict[str, Any]:
        if idempotency_key is not None and idempotency_key in self.journal:
            return self.journal[idempotency_key]
        # Как настоящее расширение: задача для другого аккаунта площадки, чем открыт в
        # браузере, не выполняется.
        if account is not None and account != self.external_user_id:
            raise OpError("session_mismatch")
        result = self._run(op, args)
        self.executed.append(op)
        if op in _WRITE_OPS and idempotency_key is not None:
            self.journal[idempotency_key] = result
        return result

    def _run(self, op: str, args: Mapping[str, Any]) -> dict[str, Any]:
        catalog = [self._track(t) for t in DEMO_TRACKS]
        if op == "search":
            title = str(args.get("title", "")).lower()
            return {"tracks": [t for t in catalog if t["title"].lower() == title]}
        if op == "search_by_isrc":
            return {"tracks": [t for t in catalog if t["isrc"] == args.get("isrc")]}
        if op == "playlist_info":
            playlist_id = str(args["playlist_id"])
            if playlist_id not in self.playlists:
                raise OpError("not_found")
            return {
                "title": self.titles[playlist_id],
                "owner_external_id": self.external_user_id,
                "track_count": len(self.playlists[playlist_id]),
            }
        if op == "is_own_library":
            return {"own": False}
        if op == "playlist_page":
            playlist_id = str(args["playlist_id"])
            if playlist_id not in self.playlists:
                raise OpError("not_found")
            by_id = {t["id"]: t for t in catalog}
            tracks = [by_id[i] for i in self.playlists[playlist_id] if i in by_id]
            page, next_cursor = self._page(tracks, args.get("cursor"))
            return {"tracks": page, "next_cursor": next_cursor, "title": self.titles[playlist_id]}
        if op == "library_page":
            by_id = {t["id"]: t for t in catalog}
            page, next_cursor = self._page(
                [by_id[i] for i in self.library if i in by_id], args.get("cursor")
            )
            return {"tracks": page, "next_cursor": next_cursor}
        if op == "create_playlist":
            playlist_id = f"pl-{len(self.playlists) + 1}"
            self.playlists[playlist_id] = []
            self.titles[playlist_id] = str(args["title"])
            return {"playlist_id": playlist_id}
        if op == "add_tracks":
            target = self.playlists.get(str(args["playlist_id"]))
            if target is None:
                raise OpError("not_found")
            for track_id in args["track_ids"]:
                if track_id not in target:  # уже добавленное не добавляется
                    target.append(track_id)
            return {"added": list(args["track_ids"]), "failed": []}
        if op == "add_to_library":
            for track_id in args["track_ids"]:
                if track_id not in self.library:
                    self.library.insert(0, track_id)
            return {"added": list(args["track_ids"]), "failed": []}
        raise OpError("unsupported_op", op)


@dataclass
class FakeSoundCloudBrowser(FakeBrowserPlatform):
    """SoundCloud «в браузере» для гибридного шлюза (4c-3): операции отвечают проекциями
    формы api-v2 (integrations/platforms/soundcloud/extension_wire.py). Сеты — id → список
    id треков; лайки — свежие сверху."""

    platform: Platform = Platform.SOUNDCLOUD
    external_user_id: str = "900001"
    sets: dict[int, list[int]] = field(default_factory=dict)
    set_titles: dict[int, str] = field(default_factory=dict)
    likes: list[int] = field(default_factory=list)

    @staticmethod
    def _sc_track(track_id: int) -> dict[str, Any]:
        return {
            "id": track_id,
            "kind": "track",
            "title": f"Track {track_id}",
            "duration": 200_000,
            "full_duration": 200_000,
            "policy": "ALLOW",
            "user": {"id": 1, "username": "Artist"},
        }

    def _set(self, args: Mapping[str, Any]) -> list[int]:
        tracks = self.sets.get(int(args["playlist_id"]))
        if tracks is None or args.get("secret") != f"s-{args['playlist_id']}":
            raise OpError("not_found")
        return tracks

    def _run(self, op: str, args: Mapping[str, Any]) -> dict[str, Any]:
        own = int(self.external_user_id)
        if op == "whoami":
            return {"id": own, "username": "Fake Listener", "permalink": "fake-listener"}
        if op == "liked_tracks_page":
            page, next_cursor = self._page(
                [self._sc_track(i) for i in self.likes], args.get("cursor")
            )
            return {"tracks": page, "next_cursor": next_cursor}
        if op == "liked_track_ids_page":
            return {"ids": list(self.likes), "next_cursor": None}
        if op == "playlist":
            tracks = self._set(args)
            playlist_id = int(args["playlist_id"])
            return {
                "id": playlist_id,
                "kind": "playlist",
                "title": self.set_titles[playlist_id],
                "user_id": own,
                "secret": f"s-{playlist_id}",
                "track_count": len(tracks),
                "tracks": [{"id": i, "kind": "track", "policy": "ALLOW"} for i in tracks],
            }
        if op == "tracks":
            return {"tracks": [self._sc_track(int(i)) for i in args["ids"]]}
        if op == "create_playlist":
            playlist_id = 5000 + len(self.sets) + 1
            self.sets[playlist_id] = []
            self.set_titles[playlist_id] = str(args["title"])
            return {"id": playlist_id, "secret": f"s-{playlist_id}"}
        if op == "set_playlist_tracks":
            self._set(args)[:] = [int(i) for i in args["track_ids"]]
            return {}
        if op == "like":
            track_id = int(args["track_id"])
            if track_id not in self.likes:
                self.likes.insert(0, track_id)
            return {}
        raise OpError("unsupported_op", op)


async def run_task(
    platform: FakeBrowserPlatform,
    op: str,
    args: Mapping[str, Any],
    key: str | None,
    account: str | None = None,
) -> dict[str, Any]:
    """Задача → результат в форме протокола (ok/data или ok=false/error)."""
    prefix, _, short = op.partition(".")
    if prefix != platform.platform.value:
        return {"ok": False, "error": {"code": "unsupported_op", "message": op}}
    try:
        return {"ok": True, "data": await platform.execute(short, args, key, account)}
    except OpError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": exc.message}}


@dataclass
class HubDevice:
    """Расширение, подключённое к хабу напрямую (без WS) — для интеграционных тестов.
    `lose_result_of` — операции, результат которых «теряется»: выполнена в браузере, а
    до сервера ответ не дошёл (браузер закрыли)."""

    hub: ExtensionHub
    user_id: UUID
    device_id: UUID
    platform: FakeBrowserPlatform
    lose_result_of: set[str] = field(default_factory=set)
    lost: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def go_online(self, session: SessionState = SessionState.OK) -> None:
        await self.hub.requeue_inflight(self.device_id)
        await self.hub.publish_presence(
            self.user_id,
            self.device_id,
            [PlatformPresence(self.platform.platform, session, self.platform.external_user_id)],
            ttl_seconds=60,
        )

    async def go_offline(self) -> None:
        await self.hub.drop_presence(self.user_id, self.device_id, [self.platform.platform])

    async def serve(self, *, stop_after_loss: bool = False) -> None:
        while True:
            task = await self.hub.next_task(self.device_id, wait_seconds=1)
            if task is None:
                continue
            outcome = await run_task(
                self.platform, task.op, task.args, task.idempotency_key, task.account
            )
            short = task.op.partition(".")[2]
            if short in self.lose_result_of:
                self.lose_result_of.discard(short)
                self.lost.append((task.task_id, outcome))
                if stop_after_loss:
                    await self.go_offline()
                    return
                continue
            await self.hub.complete(self.device_id, task.task_id, outcome)

    async def flush_lost(self) -> None:
        """Переподключились — досылаем результаты, которые не успели отправить."""
        for task_id, outcome in self.lost:
            await self.hub.complete(self.device_id, task_id, outcome)
        self.lost.clear()


# --------------------------------------------------------------------------- WebSocket


class WsFakeExtension:
    """То же по настоящему WebSocket — для сквозного теста против запущенного api."""

    def __init__(self, ws_url: str, token: str, platform: FakeBrowserPlatform) -> None:
        self._ws_url = ws_url
        self._token = token
        self.platform = platform
        self.lose_result_of: set[str] = set()
        self.lost: list[dict[str, Any]] = []
        self.connected_accounts: list[dict[str, Any]] = []

    async def run(self, *, connect_platform: bool, until: Callable[[], Awaitable[bool]]) -> str:
        """Работает, пока until() не вернёт True или не «потеряется» результат (тогда
        соединение рвётся, как при закрытом браузере). Возвращает причину выхода."""
        from websockets.asyncio.client import connect  # uvicorn[standard] ставит websockets

        async with connect(self._ws_url) as ws:
            await ws.send(json.dumps({"type": "hello", "token": self._token, "version": "fake-1"}))
            welcome = json.loads(await ws.recv())
            assert welcome["type"] == "welcome", welcome
            for outcome in self.lost:
                await ws.send(json.dumps({"type": "result", **outcome}))
            self.lost.clear()
            state = {
                "type": "platform_state",
                "platform": self.platform.platform.value,
                "session": "ok",
                "external_user_id": self.platform.external_user_id,
            }
            await ws.send(json.dumps(state))
            if connect_platform:
                await ws.send(
                    json.dumps(
                        {
                            "type": "connect_platform",
                            "request_id": "r1",
                            "platform": self.platform.platform.value,
                            "external_user_id": self.platform.external_user_id,
                            "display_name": "Fake extension user",
                        }
                    )
                )
            last_ping = time.monotonic()
            while True:
                if await until():
                    return "done"
                if time.monotonic() - last_ping > 15:
                    await ws.send(json.dumps({"type": "ping"}))
                    last_ping = time.monotonic()
                try:
                    message = json.loads(await asyncio.wait_for(ws.recv(), 1.0))
                except TimeoutError:
                    continue
                if message["type"] == "platform_connected":
                    self.connected_accounts.append(message)
                elif message["type"] == "error":
                    print("сервер:", message, file=sys.stderr)
                elif message["type"] == "task":
                    outcome = await run_task(
                        self.platform,
                        message["op"],
                        message["args"],
                        message["idempotency_key"],
                        message.get("account"),
                    )
                    short = message["op"].partition(".")[2]
                    reply = {"task_id": message["task_id"], **outcome}
                    if short in self.lose_result_of:
                        self.lose_result_of.discard(short)
                        self.lost.append(reply)
                        return "lost_result"  # браузер «закрыли» сразу после записи
                    await ws.send(json.dumps({"type": "result", **reply}))


# --------------------------------------------------------------------------- e2e


async def _e2e(api: str) -> int:
    import httpx

    ws_url = api.replace("http", "ws", 1).rstrip("/") + "/extension/ws"
    async with httpx.AsyncClient(base_url=api, timeout=30) as http:
        email = f"ext-e2e-{uuid.uuid4().hex[:8]}@example.com"
        password = "e2e-password-123456"
        (
            await http.post("/auth/register", json={"email": email, "password": password})
        ).raise_for_status()
        print("пользователь:", email)

        # Источник — фейковый Spotify по токену (PLATFORMS__FAKE содержит spotify).
        (
            await http.post("/accounts", json={"platform": "spotify", "access_token": "e2e-token"})
        ).raise_for_status()

        # Привязка: расширение получает код → пользователь вводит его на «сайте» → токен.
        started = (
            await http.post(
                "/extension/pairings",
                json={"device_name": "Fake browser", "browser": "fake", "version": "fake-1"},
            )
        ).json()
        print("код привязки:", started["user_code"])
        (
            await http.post("/extension/pairings/confirm", json={"user_code": started["user_code"]})
        ).raise_for_status()
        claimed = (
            await http.post("/extension/pairings/token", json={"pairing_id": started["pairing_id"]})
        ).json()
        assert claimed["status"] == "paired", claimed
        me = (
            await http.get(
                "/extension/me", headers={"Authorization": f"Device {claimed['device_token']}"}
            )
        ).json()
        print("расширение привязано к:", me["user_email"])

        platform = FakeBrowserPlatform(Platform.VK, external_user_id=f"vk-{uuid.uuid4().hex[:6]}")
        extension = WsFakeExtension(ws_url, claimed["device_token"], platform)

        async def connected() -> bool:
            return bool(extension.connected_accounts)

        await extension.run(connect_platform=True, until=connected)
        print("VK подключён через расширение:", extension.connected_accounts[0]["account_id"])

        transfer = (
            await http.post(
                "/transfers",
                json={
                    "source": {"kind": "playlist", "platform": "spotify", "external_id": "demo"},
                    "destination": {
                        "kind": "new",
                        "platform": "vk",
                        "title": "E2E через расширение",
                    },
                },
            )
        ).json()
        transfer_id = transfer["id"]
        print("перенос:", transfer_id)

        async def status() -> str:
            body = (await http.get(f"/transfers/{transfer_id}")).json()
            return str(body["status"])

        # 1) Браузер «закрывают» сразу после создания плейлиста: ответ не дошёл.
        extension.lose_result_of.add("create_playlist")

        async def never() -> bool:
            return False

        reason = await extension.run(connect_platform=False, until=never)
        assert reason == "lost_result", reason
        print("браузер закрыт сразу после создания плейлиста")
        for _ in range(60):
            if await status() == "paused_client":
                break
            await asyncio.sleep(1)
        current = await status()
        print("статус переноса без браузера:", current)
        if current != "paused_client":
            print("ОШИБКА: ожидали paused_client", file=sys.stderr)
            return 1

        # 2) Браузер открыли снова — перенос продолжается сам.
        async def finished() -> bool:
            return await status() in ("done", "failed")

        with contextlib.suppress(Exception):
            await asyncio.wait_for(extension.run(connect_platform=False, until=finished), 120)
        final = (await http.get(f"/transfers/{transfer_id}")).json()
        print("итог:", final["status"], "| плейлистов на площадке:", len(platform.playlists))
        print("треков в плейлисте:", {k: len(v) for k, v in platform.playlists.items()})
        ok = final["status"] == "done" and len(platform.playlists) == 1
        print("OK" if ok else "ОШИБКА", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Фейковое расширение SyncPlaylists")
    sub = parser.add_subparsers(dest="command", required=True)
    e2e = sub.add_parser("e2e", help="сквозной тест против запущенного api")
    e2e.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()
    if args.command == "e2e":
        sys.exit(asyncio.run(_e2e(args.api)))


if __name__ == "__main__":
    main()
