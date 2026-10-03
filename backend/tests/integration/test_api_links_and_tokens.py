"""HTTP-уровень этапа 4b: подключение по токену с проверкой профиля, /links/resolve,
перенос по ссылке. Яндекс — настоящий адаптер, его API подменяет respx; остальные
площадки — фейк (settings.platforms.fake в tests/integration/conftest.py)."""

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import respx
from httpx import ASGITransport, AsyncClient

from syncplaylists.bootstrap.api import create_app
from syncplaylists.infrastructure.config.settings import Settings

_YANDEX_API = "https://api.music.yandex.net"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "yandex"


def _fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


@pytest.fixture
def yandex_api() -> Iterator[respx.MockRouter]:
    # ASGITransport тестового клиента respx не перехватывает — только исходящие запросы
    # приложения к Яндексу.
    with respx.mock(base_url=_YANDEX_API, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        response = await c.post(
            "/auth/register",
            json={"email": f"links-{uuid4().hex}@example.com", "password": "correct horse"},
        )
        assert response.status_code == 201, response.text
        yield c


# --- POST /accounts: токен проверяется у площадки --------------------------------------


async def test_connect_yandex_fills_id_and_name_from_profile(
    client: AsyncClient, yandex_api: respx.MockRouter
) -> None:
    yandex_api.get("/account/status").respond(json=_fixture("account_status"))

    response = await client.post(
        "/accounts", json={"platform": "yandex", "access_token": "y0_valid"}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["external_user_id"] == "123456789"
    assert body["display_name"] == "Test User"
    assert "y0_valid" not in response.text


async def test_connect_ignores_client_supplied_external_id(
    client: AsyncClient, yandex_api: respx.MockRouter
) -> None:
    yandex_api.get("/account/status").respond(json=_fixture("account_status"))

    response = await client.post(
        "/accounts",
        json={"platform": "yandex", "access_token": "y0_valid", "external_user_id": "victim"},
    )

    assert response.status_code == 201
    assert response.json()["external_user_id"] == "123456789"


async def test_connect_yandex_with_rejected_token_is_422(
    client: AsyncClient, yandex_api: respx.MockRouter
) -> None:
    yandex_api.get("/account/status").respond(401, json={"error": {"name": "session-expired"}})

    response = await client.post("/accounts", json={"platform": "yandex", "access_token": "bad"})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_token"
    assert (await client.get("/accounts")).json() == []


async def test_connect_when_yandex_is_down_is_503(
    client: AsyncClient, yandex_api: respx.MockRouter
) -> None:
    yandex_api.get("/account/status").respond(502)

    response = await client.post("/accounts", json={"platform": "yandex", "access_token": "t"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "platform_unavailable"


async def test_connect_fake_platform_with_rejected_token_is_422(client: AsyncClient) -> None:
    response = await client.post("/accounts", json={"platform": "vk", "access_token": "invalid"})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_token"


# --- POST /links/resolve ----------------------------------------------------------------


async def test_resolve_link_with_connected_account_has_preview(client: AsyncClient) -> None:
    await client.post("/accounts", json={"platform": "vk", "access_token": "vk-token"})

    response = await client.post(
        "/links/resolve",
        json={"url": "Слушай: https://vk.com/music/playlist/-147845620_2949_ba3f9a6d26c5"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "platform": "vk",
        "kind": "playlist",
        "external_id": "-147845620_2949_ba3f9a6d26c5",
        "account_id": None,
        "title": "Demo playlist",
        "track_count": 3,
    }


async def test_resolve_own_yandex_likes_link_is_library(
    client: AsyncClient, yandex_api: respx.MockRouter
) -> None:
    yandex_api.get("/account/status").respond(json=_fixture("account_status"))
    account = (
        await client.post("/accounts", json={"platform": "yandex", "access_token": "y0_valid"})
    ).json()

    response = await client.post(
        "/links/resolve", json={"url": "https://music.yandex.ru/users/test.user/playlists/3"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "library"
    assert response.json()["account_id"] == account["id"]


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://music.yandex.ru/album/4766/track/57703", "not_a_playlist"),
        ("https://example.com/playlist/1", "unknown_host"),
        ("не ссылка", "invalid_url"),
        ("https://open.spotify.com/collection/tracks", "account_not_connected"),
    ],
)
async def test_resolve_bad_links_is_422_with_code(client: AsyncClient, url: str, code: str) -> None:
    response = await client.post("/links/resolve", json={"url": url})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code


# --- POST /transfers по ссылке ------------------------------------------------------------


async def test_start_transfer_from_link(client: AsyncClient) -> None:
    for platform in ("vk", "spotify"):
        await client.post("/accounts", json={"platform": platform, "access_token": "t"})

    response = await client.post(
        "/transfers",
        json={
            "source": {"kind": "link", "url": "https://vk.com/music/playlist/1_2_abc"},
            "destination": {"kind": "new", "platform": "spotify", "title": "Из VK"},
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["source"] == {
        "kind": "playlist",
        "platform": "vk",
        "external_id": "1_2_abc",
    }


async def test_start_transfer_from_bad_link_is_422(client: AsyncClient) -> None:
    response = await client.post(
        "/transfers",
        json={
            "source": {"kind": "link", "url": "https://music.youtube.com/watch?v=x&list=RDx"},
            "destination": {"kind": "new", "platform": "spotify", "title": "x"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "mix_not_supported"
