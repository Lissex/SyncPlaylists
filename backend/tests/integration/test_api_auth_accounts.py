from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from syncplaylists.bootstrap.api import create_app
from syncplaylists.infrastructure.config.settings import Settings

_PASSWORD = "correct horse battery"
_COOKIE = "sp_session"  # cookie_secure=False в тестах → без префикса __Host-

ClientFactory = Callable[[], AbstractAsyncContextManager[AsyncClient]]


@pytest.fixture
def make_client(settings: Settings) -> ClientFactory:
    app = create_app(settings)

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncClient]:
        # Отдельный клиент = отдельный «браузер» со своими cookie.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client

    return factory


def _email() -> str:
    return f"user-{uuid4().hex}@example.com"


async def _register(client: AsyncClient, email: str | None = None) -> str:
    email = email or _email()
    response = await client.post("/auth/register", json={"email": email, "password": _PASSWORD})
    assert response.status_code == 201, response.text
    return email


async def _connect(client: AsyncClient, platform: str, account: str) -> dict[str, str]:
    # Фейковая площадка выводит external_user_id из токена: разные `account` — разные
    # аккаунты площадки.
    response = await client.post(
        "/accounts",
        json={"platform": platform, "access_token": f"secret-{platform}-{account}"},
    )
    assert response.status_code == 201, response.text
    body: dict[str, str] = response.json()
    return body


# --- /auth ---


async def test_register_sets_httponly_session_cookie_and_me_works(
    make_client: ClientFactory,
) -> None:
    async with make_client() as client:
        email = _email()
        response = await client.post(
            "/auth/register", json={"email": email.upper(), "password": _PASSWORD}
        )

        assert response.status_code == 201
        set_cookie = response.headers["set-cookie"]
        assert set_cookie.startswith(f"{_COOKIE}=")
        assert "HttpOnly" in set_cookie
        assert "samesite=lax" in set_cookie.lower()
        assert "Path=/" in set_cookie
        assert "Domain" not in set_cookie

        me = await client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == email


async def test_logout_revokes_session(make_client: ClientFactory) -> None:
    async with make_client() as client:
        await _register(client)
        token = client.cookies[_COOKIE]

        assert (await client.post("/auth/logout")).status_code == 204
        # Даже если клиент сохранил старый токен — сессия на сервере удалена.
        client.cookies.set(_COOKIE, token)
        assert (await client.get("/auth/me")).status_code == 401


async def test_logout_all_revokes_every_device(make_client: ClientFactory) -> None:
    async with make_client() as laptop, make_client() as phone:
        email = await _register(laptop)
        login = await phone.post("/auth/login", json={"email": email, "password": _PASSWORD})
        assert login.status_code == 200
        assert (await phone.get("/auth/me")).status_code == 200

        assert (await laptop.post("/auth/logout-all")).status_code == 204

        assert (await phone.get("/auth/me")).status_code == 401


async def test_login_with_wrong_password_is_401(make_client: ClientFactory) -> None:
    async with make_client() as client:
        email = await _register(client)
        client.cookies.clear()

        response = await client.post("/auth/login", json={"email": email, "password": "nope"})

        assert response.status_code == 401
        assert "set-cookie" not in response.headers


async def test_duplicate_registration_is_409(make_client: ClientFactory) -> None:
    async with make_client() as client:
        email = await _register(client)

        response = await client.post("/auth/register", json={"email": email, "password": _PASSWORD})

        assert response.status_code == 409


async def test_sixth_login_attempt_in_a_minute_is_429(make_client: ClientFactory) -> None:
    async with make_client() as client:
        email = await _register(client)
        for _ in range(5):
            response = await client.post("/auth/login", json={"email": email, "password": "x"})
            assert response.status_code == 401

        response = await client.post("/auth/login", json={"email": email, "password": _PASSWORD})

        assert response.status_code == 429
        assert 0 < int(response.headers["retry-after"]) <= 60


async def test_me_without_cookie_is_401(make_client: ClientFactory) -> None:
    async with make_client() as client:
        assert (await client.get("/auth/me")).status_code == 401


async def test_cors_allows_only_configured_origin_with_credentials(
    make_client: ClientFactory,
) -> None:
    async with make_client() as client:
        allowed = await client.options(
            "/auth/login",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        denied = await client.options(
            "/auth/login",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in denied.headers


# --- /accounts ---


async def test_connect_list_disconnect_account(make_client: ClientFactory) -> None:
    async with make_client() as client:
        await _register(client)

        created = await _connect(client, "vk", "vk-1")
        listed = await client.get("/accounts")

        assert "token" not in str(created).lower()
        assert listed.status_code == 200
        assert "secret" not in listed.text
        assert [a["id"] for a in listed.json()] == [created["id"]]
        assert listed.json()[0]["status"] == "active"

        assert (await client.delete(f"/accounts/{created['id']}")).status_code == 204
        after = await client.get("/accounts")
        assert after.json()[0]["status"] == "disconnected"


async def test_second_account_on_same_platform_is_409(make_client: ClientFactory) -> None:
    async with make_client() as client:
        await _register(client)
        await _connect(client, "vk", "vk-1")

        response = await client.post(
            "/accounts",
            json={"platform": "vk", "access_token": "secret-vk-vk-2"},
        )

        assert response.status_code == 409


async def test_cannot_disconnect_foreign_account(make_client: ClientFactory) -> None:
    async with make_client() as alice, make_client() as bob:
        await _register(alice)
        await _register(bob)
        account = await _connect(alice, "vk", "vk-1")

        assert (await bob.delete(f"/accounts/{account['id']}")).status_code == 404
        assert (await alice.get("/accounts")).json()[0]["status"] == "active"


async def test_accounts_require_auth(make_client: ClientFactory) -> None:
    async with make_client() as client:
        assert (await client.get("/accounts")).status_code == 401


async def test_fake_oauth_flow_connects_official_account(make_client: ClientFactory) -> None:
    async with make_client() as client:
        await _register(client)

        start = await client.get("/accounts/spotify/oauth/start")
        assert start.status_code == 302
        provider_redirect = urlsplit(start.headers["location"])
        assert provider_redirect.path == "/accounts/spotify/oauth/callback"

        callback = await client.get(f"{provider_redirect.path}?{provider_redirect.query}")
        assert callback.status_code == 302
        assert callback.headers["location"].endswith("?connected=spotify")

        [account] = (await client.get("/accounts")).json()
        assert account["platform"] == "spotify"
        assert account["transport"] == "official"


async def test_oauth_callback_with_foreign_state_fails(make_client: ClientFactory) -> None:
    async with make_client() as alice, make_client() as mallory:
        await _register(alice)
        await _register(mallory)
        # Mallory начинает поток и подсовывает свой callback Алисе.
        start = await mallory.get("/accounts/spotify/oauth/start")
        provider_redirect = urlsplit(start.headers["location"])

        callback = await alice.get(f"{provider_redirect.path}?{provider_redirect.query}")

        assert "error=oauth_failed" in callback.headers["location"]
        assert (await alice.get("/accounts")).json() == []


async def test_oauth_start_for_platform_without_provider_is_404(
    make_client: ClientFactory,
) -> None:
    async with make_client() as client:
        await _register(client)
        assert (await client.get("/accounts/vk/oauth/start")).status_code == 404


# --- /transfers ---

_TRANSFER = {
    "source": {"kind": "playlist", "platform": "vk", "external_id": "demo"},
    "destination": {"kind": "existing", "platform": "spotify", "external_id": "demo"},
}


async def test_transfers_require_auth(make_client: ClientFactory) -> None:
    async with make_client() as client:
        assert (await client.post("/transfers", json=_TRANSFER)).status_code == 401


async def test_transfer_without_connected_accounts_is_422(make_client: ClientFactory) -> None:
    async with make_client() as client:
        await _register(client)
        await _connect(client, "vk", "vk-1")  # spotify не подключён

        assert (await client.post("/transfers", json=_TRANSFER)).status_code == 422


async def test_foreign_transfer_is_404(make_client: ClientFactory) -> None:
    async with make_client() as alice, make_client() as bob:
        await _register(alice)
        await _register(bob)
        await _connect(alice, "vk", "vk-1")
        await _connect(alice, "spotify", "sp-1")
        created = await alice.post("/transfers", json=_TRANSFER)
        assert created.status_code == 201, created.text
        transfer_id = created.json()["id"]

        assert (await alice.get(f"/transfers/{transfer_id}")).status_code == 200
        assert (await bob.get(f"/transfers/{transfer_id}")).status_code == 404
        resolve = await bob.post(f"/transfers/{transfer_id}/items/0/resolve", json={})
        assert resolve.status_code == 404
