from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import MatchingPipeline
from syncplaylists.modules.matching.domain.ports import TrackMatchRepository
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.domain.strategies import build_default_pipeline
from syncplaylists.shared_kernel.application.ports import GatewayFactory
from syncplaylists.shared_kernel.domain.value_objects import Platform


class DefaultMatchingPipelineFactory:
    def __init__(
        self,
        gateway_factory: GatewayFactory,
        track_matches: TrackMatchRepository,
        normalizer: TrackNormalizer,
        scorer: MatchScorer,
    ) -> None:
        self._gateway_factory = gateway_factory
        self._track_matches = track_matches
        self._normalizer = normalizer
        self._scorer = scorer

    def create(self, target_platform: Platform) -> MatchingPipeline:
        gateway = self._gateway_factory.for_platform(target_platform)
        return build_default_pipeline(gateway, self._track_matches, self._normalizer, self._scorer)
