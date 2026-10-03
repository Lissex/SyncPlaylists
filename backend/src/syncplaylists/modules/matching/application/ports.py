from typing import Protocol

from syncplaylists.modules.matching.domain.pipeline import MatchingPipeline
from syncplaylists.shared_kernel.application.ports import AccountAccess


class MatchingPipelineFactory(Protocol):
    # Собирает пайплайн под конкретный аккаунт целевой площадки — вызывающему коду
    # (например, transfers.MatchTransferItemUseCase) не нужно знать, из каких
    # стратегий/гейтвея/нормализатора/скорера он строится.
    def create(self, target: AccountAccess) -> MatchingPipeline: ...
