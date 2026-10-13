"""HTTP чтения для сайта (этап 5-0): история, страница треков, явная запись."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from syncplaylists.bootstrap.api import create_app
from syncplaylists.infrastructure.config.settings import Settings

ClientFactory = Callable[[], AbstractAsyncContextManager[AsyncClient]]


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


async def _logged_in(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={"email": f"user-{uuid4().hex}@example.com", "password": "correct horse battery"},
    )
    assert response.status_code == 201, response.text


async def test_site_endpoints_require_login(make_client: ClientFactory) -> None:
    async with make_client() as anonymous:
        transfer_id = uuid4()
        assert (await anonymous.get("/transfers")).status_code == 401
        assert (await anonymous.get(f"/transfers/{transfer_id}/items")).status_code == 401
        assert (await anonymous.post(f"/transfers/{transfer_id}/write")).status_code == 401


async def test_empty_history_and_unknown_transfer(make_client: ClientFactory) -> None:
    async with make_client() as site:
        await _logged_in(site)

        history = await site.get("/transfers")
        assert history.status_code == 200
        assert history.json() == {"items": [], "next_cursor": None}

        transfer_id = uuid4()
        items = await site.get(f"/transfers/{transfer_id}/items?status=uncertain")
        assert items.status_code == 404
        assert (await site.post(f"/transfers/{transfer_id}/write")).status_code == 404

        bad_cursor = await site.get("/transfers?cursor=not-a-cursor")
        assert bad_cursor.status_code == 422
        assert bad_cursor.json()["detail"]["code"] == "bad_cursor"
        bad_status = await site.get(f"/transfers/{transfer_id}/items?status=whatever")
        assert bad_status.status_code == 422


async def test_recognition_retry_endpoint(make_client: ClientFactory) -> None:
    """«Ещё раз по звуку» (этап 6-2): вход обязателен, чужой/несуществующий перенос — 404."""
    transfer_id = uuid4()
    async with make_client() as anonymous:
        response = await anonymous.post(f"/transfers/{transfer_id}/items/0/recognize")
        assert response.status_code == 401
    async with make_client() as site:
        await _logged_in(site)
        response = await site.post(f"/transfers/{transfer_id}/items/0/recognize")
        assert response.status_code == 404
