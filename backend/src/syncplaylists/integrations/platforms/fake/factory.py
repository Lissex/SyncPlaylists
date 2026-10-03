from syncplaylists.integrations.platforms.fake.gateway import FakeMusicPlatformGateway
from syncplaylists.shared_kernel.application.ports import AccountAccess
from syncplaylists.shared_kernel.domain.ports import MusicPlatformGateway


class FakeGatewayFactory:
    def for_account(self, access: AccountAccess) -> MusicPlatformGateway:
        # Фейку credentials не нужны — важна только площадка.
        return FakeMusicPlatformGateway(access.platform)
