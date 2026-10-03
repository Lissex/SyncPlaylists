from uuid import UUID, uuid4

import pytest

from syncplaylists.modules.accounts.application.access import AccountAccessService
from syncplaylists.modules.accounts.application.use_cases import (
    CompleteOAuthUseCase,
    ConnectAccountUseCase,
    DisconnectAccountUseCase,
    ListAccountsUseCase,
    OAuthFlowConfig,
    OAuthFlowError,
    OAuthProviderNotFoundError,
    StartOAuthUseCase,
)
from syncplaylists.modules.accounts.domain.errors import (
    AccountAlreadyConnectedError,
    AccountNotFoundError,
)
from syncplaylists.modules.accounts.domain.value_objects import AccountStatus
from syncplaylists.shared_kernel.application.ports import (
    AccountAccess,
    AccountNotAvailableError,
    PlatformCredentials,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport
from tests.fakes import FakeUnitOfWork
from tests.fakes.accounts import (
    DictRegistry,
    FakeTokenCipher,
    InMemoryConnectedAccountRepository,
    InMemoryCredentialsWriter,
    InMemoryOAuthStateStore,
    StubOAuthProvider,
)

_CONFIG = OAuthFlowConfig(callback_base_url="http://api.test/", state_ttl_seconds=600)


class Env:
    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.accounts = InMemoryConnectedAccountRepository()
        self.cipher = FakeTokenCipher()
        self.writer = InMemoryCredentialsWriter(self.accounts)
        self.states = InMemoryOAuthStateStore()
        self.provider = StubOAuthProvider(Platform.SPOTIFY)
        self.registry = DictRegistry(self.provider)
        self.user_id = uuid4()

    def connect_use_case(self) -> ConnectAccountUseCase:
        return ConnectAccountUseCase(self.uow, self.accounts, self.cipher)

    def access(self) -> AccountAccessService:
        return AccountAccessService(self.accounts, self.cipher, self.writer)

    async def connect(
        self,
        platform: Platform = Platform.VK,
        external_user_id: str = "vk-1",
        user_id: UUID | None = None,
        access_token: str = "secret-access",
    ) -> UUID:
        dto = await self.connect_use_case().execute(
            user_id=user_id or self.user_id,
            platform=platform,
            transport=Transport.UNOFFICIAL,
            external_user_id=external_user_id,
            display_name="Alice",
            credentials=PlatformCredentials(access_token=access_token, refresh_token="secret-r"),
        )
        return dto.id


async def test_connect_stores_only_encrypted_tokens() -> None:
    env = Env()

    account_id = await env.connect()

    account = env.accounts.storage[account_id]
    assert account.access_token is not None
    assert b"secret-access" not in account.access_token.ciphertext
    assert env.uow.commits == 1


async def test_connect_dto_has_no_tokens() -> None:
    env = Env()
    await env.connect()

    [dto] = await ListAccountsUseCase(env.accounts).execute(env.user_id)

    assert "secret" not in repr(dto)
    assert not hasattr(dto, "access_token")


async def test_connect_second_account_on_same_platform_conflicts() -> None:
    env = Env()
    await env.connect(external_user_id="vk-1")

    with pytest.raises(AccountAlreadyConnectedError):
        await env.connect(external_user_id="vk-2")


async def test_connect_same_external_account_reconnects_in_place() -> None:
    env = Env()
    first_id = await env.connect(external_user_id="vk-1", access_token="old")
    await DisconnectAccountUseCase(env.uow, env.accounts).execute(env.user_id, first_id)

    second_id = await env.connect(external_user_id="vk-1", access_token="new")

    assert second_id == first_id
    access = await env.access().get(env.user_id, first_id)
    assert access.credentials.access_token == "new"


async def test_after_disconnect_another_account_can_be_connected() -> None:
    env = Env()
    first_id = await env.connect(external_user_id="vk-1")
    await DisconnectAccountUseCase(env.uow, env.accounts).execute(env.user_id, first_id)

    second_id = await env.connect(external_user_id="vk-2")

    assert second_id != first_id


async def test_disconnect_foreign_account_is_not_found() -> None:
    env = Env()
    account_id = await env.connect()

    with pytest.raises(AccountNotFoundError):
        await DisconnectAccountUseCase(env.uow, env.accounts).execute(uuid4(), account_id)
    assert env.accounts.storage[account_id].status is AccountStatus.ACTIVE


async def test_access_decrypts_credentials() -> None:
    env = Env()
    account_id = await env.connect(access_token="secret-access")

    access = await env.access().get(env.user_id, account_id)

    assert access.credentials.access_token == "secret-access"
    assert access.credentials.refresh_token == "secret-r"
    assert access.platform is Platform.VK


async def test_access_for_platform_finds_active_account() -> None:
    env = Env()
    account_id = await env.connect(platform=Platform.YANDEX, external_user_id="ya-1")

    access = await env.access().for_platform(env.user_id, Platform.YANDEX)

    assert access.account_id == account_id


async def test_access_to_foreign_or_missing_account_is_unavailable() -> None:
    env = Env()
    account_id = await env.connect()

    with pytest.raises(AccountNotAvailableError):
        await env.access().get(uuid4(), account_id)
    with pytest.raises(AccountNotAvailableError):
        await env.access().get(env.user_id, uuid4())
    with pytest.raises(AccountNotAvailableError):
        await env.access().for_platform(env.user_id, Platform.SPOTIFY)


async def test_access_to_disconnected_account_is_unavailable() -> None:
    env = Env()
    account_id = await env.connect()
    await DisconnectAccountUseCase(env.uow, env.accounts).execute(env.user_id, account_id)

    with pytest.raises(AccountNotAvailableError):
        await env.access().get(env.user_id, account_id)
    with pytest.raises(AccountNotAvailableError):
        await env.access().for_platform(env.user_id, Platform.VK)


async def test_ciphertext_is_bound_to_its_account() -> None:
    # AAD = account_id + поле: шифротекст, переставленный в другую строку, не расшифруется.
    env = Env()
    first = await env.connect(platform=Platform.VK, external_user_id="vk-1")
    second = await env.connect(platform=Platform.YANDEX, external_user_id="ya-1")
    env.accounts.storage[second].access_token = env.accounts.storage[first].access_token

    with pytest.raises(ValueError, match="AAD"):
        await env.access().get(env.user_id, second)


async def test_update_credentials_reencrypts_through_writer() -> None:
    env = Env()
    account_id = await env.connect()

    await env.access().update_credentials(
        account_id, PlatformCredentials(access_token="rotated", refresh_token=None)
    )

    assert env.writer.calls == 1
    access = await env.access().get(env.user_id, account_id)
    assert access.credentials.access_token == "rotated"
    assert access.credentials.refresh_token == "secret-r"  # не ротирован — старый в силе


async def test_update_credentials_for_missing_account_is_unavailable() -> None:
    env = Env()

    with pytest.raises(AccountNotAvailableError):
        await env.access().update_credentials(uuid4(), PlatformCredentials(access_token="x"))


def test_account_access_repr_hides_tokens() -> None:
    access = AccountAccess(
        account_id=uuid4(),
        user_id=uuid4(),
        platform=Platform.VK,
        transport=Transport.UNOFFICIAL,
        credentials=PlatformCredentials(access_token="top-secret", refresh_token="also-secret"),
    )

    assert "top-secret" not in repr(access)
    assert "also-secret" not in repr(access)


# --- OAuth ---


def _oauth(env: Env) -> tuple[StartOAuthUseCase, CompleteOAuthUseCase]:
    start = StartOAuthUseCase(env.registry, env.states, _CONFIG)
    complete = CompleteOAuthUseCase(env.registry, env.states, _CONFIG, env.connect_use_case())
    return start, complete


async def test_oauth_start_saves_state_with_pkce_verifier() -> None:
    env = Env()
    start, _ = _oauth(env)

    url = await start.execute(env.user_id, Platform.SPOTIFY)

    [(state, pending)] = env.states.storage.items()
    assert f"state={state}" in url
    assert pending.user_id == env.user_id
    assert 43 <= len(pending.code_verifier) <= 128
    assert pending.code_verifier not in url  # в URL только challenge


async def test_oauth_start_unknown_platform() -> None:
    env = Env()
    start, _ = _oauth(env)

    with pytest.raises(OAuthProviderNotFoundError):
        await start.execute(env.user_id, Platform.VK)


async def test_oauth_complete_connects_official_account() -> None:
    env = Env()
    start, complete = _oauth(env)
    await start.execute(env.user_id, Platform.SPOTIFY)
    [(state, pending)] = env.states.storage.items()

    dto = await complete.execute(env.user_id, Platform.SPOTIFY, state, "the-code")

    assert dto.transport is Transport.OFFICIAL
    assert dto.external_user_id == "ext-1"
    assert env.provider.exchanged == [
        ("the-code", pending.code_verifier, "http://api.test/accounts/spotify/oauth/callback")
    ]
    assert env.states.storage == {}  # state одноразовый


async def test_oauth_complete_rejects_state_of_another_user() -> None:
    env = Env()
    start, complete = _oauth(env)
    await start.execute(env.user_id, Platform.SPOTIFY)
    [state] = env.states.storage

    with pytest.raises(OAuthFlowError):
        await complete.execute(uuid4(), Platform.SPOTIFY, state, "the-code")
    assert env.provider.exchanged == []


async def test_oauth_complete_rejects_unknown_or_reused_state() -> None:
    env = Env()
    start, complete = _oauth(env)
    await start.execute(env.user_id, Platform.SPOTIFY)
    [state] = env.states.storage
    await complete.execute(env.user_id, Platform.SPOTIFY, state, "the-code")

    with pytest.raises(OAuthFlowError):
        await complete.execute(env.user_id, Platform.SPOTIFY, state, "the-code")
    with pytest.raises(OAuthFlowError):
        await complete.execute(env.user_id, Platform.SPOTIFY, "unknown", "the-code")


async def test_oauth_complete_rejects_platform_mismatch() -> None:
    env = Env()
    start, complete = _oauth(env)
    await start.execute(env.user_id, Platform.SPOTIFY)
    [state] = env.states.storage

    with pytest.raises(OAuthFlowError):
        await complete.execute(env.user_id, Platform.SOUNDCLOUD, state, "the-code")
