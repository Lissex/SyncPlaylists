from syncplaylists.modules.matching.application.pipeline_factory import (
    DefaultMatchingPipelineFactory,
)
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import MatchRequest, MatchStatus
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.shared_kernel.domain.search import TrackCandidate
from syncplaylists.shared_kernel.domain.value_objects import Duration, ExternalTrackRef, Platform
from tests.fakes import FakeGatewayFactory, FakeMusicPlatformGateway, FakeTrackMatchRepository


async def test_create_builds_a_working_pipeline_for_the_requested_platform() -> None:
    target = TrackCandidate(
        ref=ExternalTrackRef(Platform.SPOTIFY, "tgt-1"),
        title="Starboy",
        artist="The Weeknd",
        duration=Duration(230_000),
    )
    gateway_factory = FakeGatewayFactory()
    gateway_factory.register(
        FakeMusicPlatformGateway(platform=Platform.SPOTIFY, search_results=[target])
    )
    normalizer = TrackNormalizer()
    factory = DefaultMatchingPipelineFactory(
        gateway_factory, FakeTrackMatchRepository(), normalizer, MatchScorer(normalizer)
    )

    pipeline = factory.create(Platform.SPOTIFY)

    source = TrackCandidate(
        ref=ExternalTrackRef(Platform.VK, "src-1"),
        title="Starboy",
        artist="The Weeknd",
        duration=Duration(230_000),
    )
    attempt = await pipeline.run(MatchRequest(source=source, target_platform=Platform.SPOTIFY))

    assert attempt.status is MatchStatus.MATCHED
