from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from syncplaylists.modules.transfers.domain.entities import Transfer
from syncplaylists.modules.transfers.domain.value_objects import TransferStatus


class TransferRepository(Protocol):
    async def get(self, transfer_id: UUID) -> Transfer | None: ...

    # Блокирующее чтение (SELECT ... FOR UPDATE на строке transfers) — для всех use cases,
    # которые мутируют Transfer. Защищает от двойной обработки при повторной (at-least-once)
    # доставке одной и той же ARQ-джобы: вторая попытка блокируется на локе до коммита первой,
    # затем видит уже изменённый статус и может сама решить, что работа уже сделана.
    async def get_for_update(self, transfer_id: UUID) -> Transfer | None: ...

    async def save(self, transfer: Transfer) -> None: ...

    # Для sweeper-а (SweepStaleTransfersUseCase): id переносов в одном из statuses,
    # у которых updated_at старше older_than.
    async def find_stale_ids(
        self, statuses: Sequence[TransferStatus], older_than: datetime
    ) -> list[UUID]: ...
