from collections.abc import Callable, Iterable, Mapping

from syncplaylists.modules.accounts.application.ports import PlatformProfileFetcher
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.errors import PlatformNotSupportedError
from syncplaylists.shared_kernel.domain.ports import MusicPlatformGateway
from syncplaylists.shared_kernel.domain.value_objects import Platform, Transport

GatewayBuilder = Callable[[AccountAccess], MusicPlatformGateway]


class PlatformGatewayFactory:
    """shared_kernel.GatewayFactory: площадка → сборщик шлюза. Какие площадки чем
    обслуживаются (настоящий адаптер или фейк) решает bootstrap/container.py."""

    def __init__(self, builders: Mapping[Platform, GatewayBuilder]) -> None:
        self._builders = dict(builders)

    def supports(self, platform: Platform) -> bool:
        return platform in self._builders

    def for_account(self, access: AccountAccess) -> MusicPlatformGateway:
        builder = self._builders.get(access.platform)
        # Транспорт «через расширение» появится на этапе 10 — пока его не обслуживает
        # ни один адаптер.
        if builder is None or access.transport is Transport.EXTENSION:
            raise PlatformNotSupportedError(access.platform)
        return builder(access)


class DictProfileRegistry:
    """accounts.PlatformProfileRegistry."""

    def __init__(self, fetchers: Iterable[PlatformProfileFetcher]) -> None:
        self._fetchers = {fetcher.platform: fetcher for fetcher in fetchers}

    def get(self, platform: Platform) -> PlatformProfileFetcher | None:
        return self._fetchers.get(platform)
