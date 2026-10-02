from syncplaylists.integrations.platforms.fake.gateway import FakeMusicPlatformGateway
from syncplaylists.shared_kernel.domain.ports import MusicPlatformGateway
from syncplaylists.shared_kernel.domain.value_objects import Platform


class FakeGatewayFactory:
    def for_platform(self, platform: Platform) -> MusicPlatformGateway:
        return FakeMusicPlatformGateway(platform)
