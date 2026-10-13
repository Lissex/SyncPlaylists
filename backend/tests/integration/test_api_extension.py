"""HTTP привязки расширения (Postgres + Redis в testcontainers): код показывает
расширение, вводит залогиненный пользователь, токен устройства выдаётся один раз;
отозванное устройство теряет доступ."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from syncplaylists.bootstrap.api import create_app
from syncplaylists.infrastructure.config.settings import Settings

ClientFactory = Callable[[], AbstractAsyncContextManager[AsyncClient]]

_DEVICE = {"device_name": "Chrome на ноутбуке", "browser": "chrome", "version": "0.1.0"}


@pytest.fixture
def make_client(settings: Settings) -> ClientFactory:
    app = create_app(settings)

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncClient]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client

    return factory


async def _logged_in(client: AsyncClient) -> str:
    email = f"user-{uuid4().hex}@example.com"
    response = await client.post(
        "/auth/register", json={"email": email, "password": "correct horse battery"}
    )
    assert response.status_code == 201, response.text
    return email


async def test_pairing_flow(make_client: ClientFactory) -> None:
    async with make_client() as site, make_client() as extension:
        email = await _logged_in(site)

        started = await extension.post("/extension/pairings", json=_DEVICE)
        assert started.status_code == 201, started.text
        pairing = started.json()
        claim = {"pairing_id": pairing["pairing_id"]}

        pending = await extension.post("/extension/pairings/token", json=claim)
        assert pending.status_code == 202
        assert pending.json()["status"] == "pending"

        confirmed = await site.post(
            "/extension/pairings/confirm", json={"user_code": pairing["user_code"]}
        )
        assert confirmed.status_code == 204, confirmed.text

        paired = await extension.post("/extension/pairings/token", json=claim)
        assert paired.status_code == 200
        token = paired.json()["device_token"]
        assert (await extension.post("/extension/pairings/token", json=claim)).status_code == 410

        me = await extension.get("/extension/me", headers={"Authorization": f"Device {token}"})
        assert me.status_code == 200
        assert me.json()["user_email"] == email

        devices = (await site.get("/extension/devices")).json()
        assert [d["name"] for d in devices] == [_DEVICE["device_name"]]
        assert "token" not in devices[0]
        assert "token_hash" not in devices[0]

        revoked = await site.delete(f"/extension/devices/{devices[0]['id']}")
        assert revoked.status_code == 204
        after = await extension.get("/extension/me", headers={"Authorization": f"Device {token}"})
        assert after.status_code == 401


async def test_confirm_requires_login_and_valid_code(make_client: ClientFactory) -> None:
    async with make_client() as site, make_client() as anonymous:
        await _logged_in(site)
        pairing = (await anonymous.post("/extension/pairings", json=_DEVICE)).json()

        unauthorized = await anonymous.post(
            "/extension/pairings/confirm", json={"user_code": pairing["user_code"]}
        )
        assert unauthorized.status_code == 401

        wrong = await site.post("/extension/pairings/confirm", json={"user_code": "AAAA-AAAA"})
        assert wrong.status_code == 404


async def test_pairing_rejects_extra_fields(make_client: ClientFactory) -> None:
    async with make_client() as extension:
        response = await extension.post(
            "/extension/pairings", json={**_DEVICE, "cookie": "sessionid=secret"}
        )
        assert response.status_code == 422


async def test_device_me_without_token_is_unauthorized(make_client: ClientFactory) -> None:
    async with make_client() as extension:
        assert (await extension.get("/extension/me")).status_code == 401
        bad = await extension.get("/extension/me", headers={"Authorization": "Device nope"})
        assert bad.status_code == 401


async def _paired(site: AsyncClient, extension: AsyncClient) -> str:
    pairing = (await extension.post("/extension/pairings", json=_DEVICE)).json()
    confirmed = await site.post(
        "/extension/pairings/confirm", json={"user_code": pairing["user_code"]}
    )
    assert confirmed.status_code == 204, confirmed.text
    claimed = await extension.post(
        "/extension/pairings/token", json={"pairing_id": pairing["pairing_id"]}
    )
    return str(claimed.json()["device_token"])


async def test_extension_unpairs_itself(make_client: ClientFactory) -> None:
    async with make_client() as site, make_client() as extension:
        await _logged_in(site)
        auth = {"Authorization": f"Device {await _paired(site, extension)}"}

        assert (await extension.delete("/extension/me", headers=auth)).status_code == 204
        assert (await extension.get("/extension/me", headers=auth)).status_code == 401
        assert (await extension.delete("/extension/me", headers=auth)).status_code == 401
        devices = (await site.get("/extension/devices")).json()
        assert [d["revoked"] for d in devices] == [True]


async def test_status_shows_paired_device_and_platforms(make_client: ClientFactory) -> None:
    async with make_client() as site, make_client() as extension, make_client() as anonymous:
        await _logged_in(site)
        assert (await anonymous.get("/extension/status")).status_code == 401
        empty = (await site.get("/extension/status")).json()
        assert empty["devices"] == []

        await _paired(site, extension)
        status = (await site.get("/extension/status")).json()

        # WebSocket не открыт — устройство привязано, но не на связи.
        assert [(d["name"], d["online"]) for d in status["devices"]] == [
            (_DEVICE["device_name"], False)
        ]
        assert "token_hash" not in status["devices"][0]
        assert {p["platform"] for p in status["platforms"]} >= {"yandex", "vk", "soundcloud"}
        assert all(p["state"] is None for p in status["platforms"])


async def test_dev_page_and_diagnostics_are_gone(make_client: ClientFactory) -> None:
    """Этап 5-6: привязка и устройства — на сайте (фронтенд), dev-страница бэкенда и
    диагностика удалены."""
    async with make_client() as site:
        await _logged_in(site)
        assert (await site.get("/extension/pair")).status_code == 404
        assert (await site.get("/extension/diagnostics/page")).status_code == 404
        probe = await site.post(f"/extension/devices/{uuid4()}/probe")
        assert probe.status_code in (404, 405)
