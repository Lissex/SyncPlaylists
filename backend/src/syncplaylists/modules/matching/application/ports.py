from typing import Protocol

from syncplaylists.modules.matching.domain.pipeline import MatchingPipeline
from syncplaylists.shared_kernel.domain.value_objects import Platform


class MatchingPipelineFactory(Protocol):
    # Собирает пайплайн под конкретную целевую площадку — вызывающему коду (например,
    # transfers.MatchTransferItemUseCase) не нужно знать, из каких стратегий/гейтвея/
    # нормализатора/скорера он строится.
    def create(self, target_platform: Platform) -> MatchingPipeline: ...
