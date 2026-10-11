from syncplaylists.integrations.platforms.vk.api import ExtensionVkApi
from syncplaylists.integrations.platforms.vk.extension_wire import MAX_PAGE
from syncplaylists.integrations.platforms.vk.gateway import VkGateway, VkLimits
from syncplaylists.modules.extension.application.ports import ExtensionChannel
from syncplaylists.shared_kernel.application.ports import AccountAccess, PlatformWriteGuard


class VkExtensionGatewayBuilder:
    """GatewayBuilder транспорта EXTENSION для VK (4c-5) — другого у VK нет: серверного
    пути (vkpymusic, токены) не делаем. Лимитера площадки нет: запросы идут из браузера
    пользователя, темп держит расширение; суточный бюджет и flood control — guard."""

    def __init__(
        self,
        channel: ExtensionChannel,
        guard: PlatformWriteGuard,
        limits: VkLimits,
        *,
        page_size: int = MAX_PAGE,
    ) -> None:
        self._channel = channel
        self._guard = guard
        self._limits = limits
        self._page_size = page_size

    def __call__(self, access: AccountAccess) -> VkGateway:
        return VkGateway(
            ExtensionVkApi(self._channel, access, page_size=self._page_size),
            access.external_user_id,
            access.account_id,
            self._guard,
            self._limits,
        )
