from collections.abc import Callable, Iterable, Mapping

from syncplaylists.modules.accounts.application.ports import PlatformProfileFetcher
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.errors import PlatformNotSupportedError
from syncplaylists.shared_kernel.domain.ports import MusicPlatformGateway
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport

GatewayBuilder = Callable[[AccountAccess], MusicPlatformGateway]


class PlatformGatewayFactory:
    """shared_kernel.GatewayFactory: (площадка, транспорт) → сборщик шлюза. Аккаунты с
    транспортом EXTENSION (запросы из браузера пользователя) обслуживают свои сборщики,
    остальные — серверные адаптеры. Что чем обслуживается (настоящий адаптер или фейк)
    решает bootstrap/container.py."""

    def __init__(
        self,
        builders: Mapping[Platform, GatewayBuilder],
        extension_builders: Mapping[Platform, GatewayBuilder] | None = None,
    ) -> None:
        self._builders = dict(builders)
        self._extension_builders = dict(extension_builders or {})

    def supports(self, platform: Platform) -> bool:
        return platform in self._builders or platform in self._extension_builders

    def for_account(self, access: AccountAccess) -> MusicPlatformGateway:
        builders = (
            self._extension_builders if access.transport is Transport.EXTENSION else self._builders
        )
        builder = builders.get(access.platform)
        if builder is None:
            raise PlatformNotSupportedError(access.platform)
        return builder(access)


class DictProfileRegistry:
    """accounts.PlatformProfileRegistry."""

    def __init__(self, fetchers: Iterable[PlatformProfileFetcher]) -> None:
        self._fetchers = {fetcher.platform: fetcher for fetcher in fetchers}

    def get(self, platform: Platform) -> PlatformProfileFetcher | None:
        return self._fetchers.get(platform)
