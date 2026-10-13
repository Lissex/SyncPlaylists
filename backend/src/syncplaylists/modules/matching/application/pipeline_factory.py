from syncplaylists.modules.matching.domain.audio import AudioMatcher
from syncplaylists.modules.matching.domain.normalization import TrackNormalizer
from syncplaylists.modules.matching.domain.pipeline import MatchingPipeline
from syncplaylists.modules.matching.domain.ports import TrackMatchRepository
from syncplaylists.modules.matching.domain.scoring import MatchScorer
from syncplaylists.modules.matching.domain.strategies import build_default_pipeline
from syncplaylists.shared_kernel.application.ports import AccountAccess, GatewayFactory


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

    def create(self, target: AccountAccess) -> MatchingPipeline:
        gateway = self._gateway_factory.for_account(target)
        return build_default_pipeline(gateway, self._track_matches, self._normalizer, self._scorer)

    def create_audio(self, target: AccountAccess) -> AudioMatcher:
        gateway = self._gateway_factory.for_account(target)
        return AudioMatcher(gateway, self._normalizer, self._scorer)
