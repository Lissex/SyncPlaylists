from tests.fakes.catalog import FakeCanonicalTrackRepository, FakePlatformTrackRepository
from tests.fakes.matching import FakeTrackMatchRepository
from tests.fakes.platform import FakeGatewayFactory, FakeMusicPlatformGateway
from tests.fakes.transfers import (
    FakeEventPublisher,
    FakeTaskQueue,
    FakeTransferRepository,
    FakeUnitOfWork,
)

__all__ = [
    "FakeCanonicalTrackRepository",
    "FakeEventPublisher",
    "FakeGatewayFactory",
    "FakeMusicPlatformGateway",
    "FakePlatformTrackRepository",
    "FakeTaskQueue",
    "FakeTrackMatchRepository",
    "FakeTransferRepository",
    "FakeUnitOfWork",
]
