import hashlib

from syncplaylists.integrations.platforms.fake.gateway import FakeMusicPlatformGateway
from syncplaylists.modules.accounts.application.ports import PlatformProfile
from syncplaylists.shared_kernel.application.ports import AccountAccess, PlatformCredentials
from syncplaylists.shared_kernel.domain.errors import PlatformAuthError
from syncplaylists.shared_kernel.domain.value_objects import Platform


def build_fake_gateway(access: AccountAccess) -> FakeMusicPlatformGateway:
    # Фейку credentials не нужны — важны площадка и «свой» аккаунт.
    return FakeMusicPlatformGateway(access.platform, access.external_user_id)


class FakeProfileFetcher:
    """Профиль без площадки (dev/тесты): id детерминирован от токена — повторное
    подключение тем же токеном попадает в тот же аккаунт. Токен с префиксом
    "invalid" площадка «не принимает»."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform

    async def fetch(self, credentials: PlatformCredentials) -> PlatformProfile:
        token = credentials.access_token
        if token.startswith("invalid"):
            raise PlatformAuthError(self.platform, "фейк: токен не принят")
        digest = hashlib.sha256(token.encode()).hexdigest()[:12]
        return PlatformProfile(
            external_user_id=f"fake-{self.platform.value}-{digest}",
            display_name=f"Fake {self.platform.value} user",
        )
