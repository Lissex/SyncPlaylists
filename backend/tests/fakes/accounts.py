from collections.abc import Callable
from uuid import UUID, uuid4

from syncplaylists.modules.accounts.application.ports import (
    OAuthGrant,
    PendingOAuth,
)
from syncplaylists.modules.accounts.domain.entities import ConnectedAccount
from syncplaylists.modules.accounts.domain.value_objects import AccountStatus
from syncplaylists.shared_kernel.application.ports import (
    AccountAccess,
    AccountNotAvailableError,
    PlatformCredentials,
)
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport


def make_access(user_id: UUID, platform: Platform, account_id: UUID | None = None) -> AccountAccess:
    return AccountAccess(
        account_id=account_id or uuid4(),
        user_id=user_id,
        platform=platform,
        transport=Transport.UNOFFICIAL,
        credentials=PlatformCredentials(access_token=f"token-{platform.value}"),
    )


class FakeAccountAccessProvider:
    def __init__(self) -> None:
        self._accesses: dict[UUID, AccountAccess] = {}
        self._unavailable: set[UUID] = set()
        self.updated: list[tuple[UUID, PlatformCredentials]] = []

    def connect(self, user_id: UUID, platform: Platform) -> AccountAccess:
        access = make_access(user_id, platform)
        self._accesses[access.account_id] = access
        return access

    def disconnect(self, account_id: UUID) -> None:
        self._unavailable.add(account_id)

    async def get(self, user_id: UUID, account_id: UUID) -> AccountAccess:
        access = self._accesses.get(account_id)
        if access is None or access.user_id != user_id or account_id in self._unavailable:
            raise AccountNotAvailableError(str(account_id))
        return access

    async def for_platform(self, user_id: UUID, platform: Platform) -> AccountAccess:
        for access in self._accesses.values():
            if (
                access.user_id == user_id
                and access.platform is platform
                and access.account_id not in self._unavailable
            ):
                return access
        raise AccountNotAvailableError(f"{user_id}/{platform}")

    async def update_credentials(self, account_id: UUID, credentials: PlatformCredentials) -> None:
        self.updated.append((account_id, credentials))


class FakeTokenCipher:
    """Обратимое «шифрование» с проверкой AAD — без криптографии, но ловит перепутанные
    AAD так же, как настоящий AES-GCM."""

    def encrypt(self, plaintext: str, *, aad: bytes) -> bytes:
        return b"enc|" + aad + b"|" + plaintext.encode()[::-1]

    def decrypt(self, ciphertext: bytes, *, aad: bytes) -> str:
        prefix = b"enc|" + aad + b"|"
        if not ciphertext.startswith(prefix):
            raise ValueError("AAD не совпадает")
        return ciphertext[len(prefix) :][::-1].decode()


class InMemoryConnectedAccountRepository:
    def __init__(self) -> None:
        self.storage: dict[UUID, ConnectedAccount] = {}

    async def get(self, account_id: UUID) -> ConnectedAccount | None:
        return self.storage.get(account_id)

    async def find_by_external(
        self, user_id: UUID, platform: Platform, external_user_id: str
    ) -> ConnectedAccount | None:
        return next(
            (
                a
                for a in self.storage.values()
                if a.user_id == user_id
                and a.platform is platform
                and a.external_user_id == external_user_id
            ),
            None,
        )

    async def find_active(self, user_id: UUID, platform: Platform) -> ConnectedAccount | None:
        return next(
            (
                a
                for a in self.storage.values()
                if a.user_id == user_id
                and a.platform is platform
                and a.status is AccountStatus.ACTIVE
            ),
            None,
        )

    async def list_for_user(self, user_id: UUID) -> list[ConnectedAccount]:
        return [a for a in self.storage.values() if a.user_id == user_id]

    async def add(self, account: ConnectedAccount) -> None:
        self.storage[account.id] = account

    async def save(self, account: ConnectedAccount) -> None:
        self.storage[account.id] = account


class InMemoryCredentialsWriter:
    def __init__(self, accounts: InMemoryConnectedAccountRepository) -> None:
        self._accounts = accounts
        self.calls = 0

    async def apply(self, account_id: UUID, change: Callable[[ConnectedAccount], None]) -> bool:
        self.calls += 1
        account = self._accounts.storage.get(account_id)
        if account is None:
            return False
        change(account)
        return True


class InMemoryOAuthStateStore:
    def __init__(self) -> None:
        self.storage: dict[str, PendingOAuth] = {}

    async def save(self, state: str, pending: PendingOAuth, ttl_seconds: int) -> None:
        self.storage[state] = pending

    async def pop(self, state: str) -> PendingOAuth | None:
        return self.storage.pop(state, None)


class StubOAuthProvider:
    def __init__(self, platform: Platform, external_user_id: str = "ext-1") -> None:
        self.platform = platform
        self._external_user_id = external_user_id
        self.exchanged: list[tuple[str, str, str]] = []

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return f"https://auth.example/authorize?state={state}&challenge={code_challenge}"

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthGrant:
        self.exchanged.append((code, code_verifier, redirect_uri))
        return OAuthGrant(
            external_user_id=self._external_user_id,
            display_name="Stub",
            access_token="oauth-access",
            refresh_token="oauth-refresh",
        )


class DictRegistry:
    def __init__(self, *providers: StubOAuthProvider) -> None:
        self._providers = {p.platform: p for p in providers}

    def get(self, platform: Platform) -> StubOAuthProvider | None:
        return self._providers.get(platform)
