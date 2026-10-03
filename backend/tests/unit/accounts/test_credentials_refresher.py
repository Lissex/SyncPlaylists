"""AccountCredentialsRefresher: OAuth refresh под блокировкой строки аккаунта — два
воркера не продлевают токен одновременно (иначе ротация refresh_token выбила бы второго)."""

import asyncio
from uuid import uuid4

import pytest

from syncplaylists.modules.accounts.application.access import AccountCredentialsRefresher
from syncplaylists.shared_kernel.application.ports import (
    AccountNotAvailableError,
    PlatformCredentials,
)
from syncplaylists.shared_kernel.domain.errors import PlatformAuthError
from syncplaylists.shared_kernel.domain.value_objects import Platform
from tests.unit.accounts.test_use_cases import Env


def _refresher(env: Env) -> AccountCredentialsRefresher:
    return AccountCredentialsRefresher(env.cipher, env.writer)


async def test_refresh_stores_new_tokens() -> None:
    env = Env()
    account_id = await env.connect()
    stale = (await env.access().get(env.user_id, account_id)).credentials

    async def renew(current: PlatformCredentials) -> PlatformCredentials:
        assert current.refresh_token == "secret-r"
        return PlatformCredentials(access_token="new-access", refresh_token="new-r")

    fresh = await _refresher(env).refresh(account_id, stale, renew)

    assert fresh.access_token == "new-access"
    stored = (await env.access().get(env.user_id, account_id)).credentials
    assert (stored.access_token, stored.refresh_token) == ("new-access", "new-r")


async def test_parallel_refresh_renews_once() -> None:
    env = Env()
    account_id = await env.connect()
    stale = (await env.access().get(env.user_id, account_id)).credentials
    renewals = 0

    async def renew(current: PlatformCredentials) -> PlatformCredentials:
        nonlocal renewals
        renewals += 1
        await asyncio.sleep(0)
        return PlatformCredentials(access_token=f"new-{renewals}", refresh_token="r2")

    results = await asyncio.gather(
        *(_refresher(env).refresh(account_id, stale, renew) for _ in range(3))
    )

    assert renewals == 1
    assert {r.access_token for r in results} == {"new-1"}


async def test_failed_renew_stores_nothing() -> None:
    env = Env()
    account_id = await env.connect()
    stale = (await env.access().get(env.user_id, account_id)).credentials

    async def renew(current: PlatformCredentials) -> PlatformCredentials:
        raise PlatformAuthError(Platform.SOUNDCLOUD, "invalid_grant")

    with pytest.raises(PlatformAuthError):
        await _refresher(env).refresh(account_id, stale, renew)
    stored = (await env.access().get(env.user_id, account_id)).credentials
    assert stored.access_token == "secret-access"


async def test_missing_account_is_unavailable() -> None:
    env = Env()

    async def renew(current: PlatformCredentials) -> PlatformCredentials:
        raise AssertionError("не должен вызываться")

    with pytest.raises(AccountNotAvailableError):
        await _refresher(env).refresh(uuid4(), PlatformCredentials(access_token="x"), renew)
