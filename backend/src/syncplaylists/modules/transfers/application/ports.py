from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from syncplaylists.modules.transfers.domain.entities import Transfer, TransferItem
from syncplaylists.modules.transfers.domain.value_objects import TransferProgress, TransferStatus


class TransferRepository(Protocol):
    async def get(self, transfer_id: UUID) -> Transfer | None: ...

    # Блокирующее чтение (SELECT ... FOR UPDATE на строке transfers) — для use cases,
    # которые мутируют Transfer целиком (run_transfer, resolve, run_write). В эти моменты
    # параллельных run_match по переносу нет: они ставятся в очередь только после
    # коммита run_transfer, а REVIEW/WRITING наступают, когда pending == 0.
    async def get_for_update(self, transfer_id: UUID) -> Transfer | None: ...

    # Полное сохранение агрегата (с items и пересчётом счётчиков). Нельзя вызывать для
    # «шапки» из get_header() — у неё нет items.
    async def save(self, transfer: Transfer) -> None: ...

    # --- точечный путь для run_match (много параллельных джоб на один перенос) ---

    # Перенос без items: run_match не тянет тысячи строк ради одного трека.
    async def get_header(self, transfer_id: UUID) -> Transfer | None: ...

    async def get_item(self, transfer_id: UUID, position: int) -> TransferItem | None: ...

    # Условно (только если в БД item ещё PENDING) сохраняет результат одного item и
    # атомарно сдвигает счётчики transfers (`pending = pending - 1, matched = matched + 1`).
    # Возвращает счётчики ПОСЛЕ своего изменения; None — item уже обработан кем-то
    # (повторная доставка/гонка), ничего не изменено. Ровно одна джоба увидит pending == 0.
    async def save_item_outcome(self, item: TransferItem) -> TransferProgress | None: ...

    # UPDATE ... SET status = to WHERE id = ? AND status = from. False — статус уже
    # другой (например, перенос параллельно упал): переход не выполнен.
    async def transition_status(
        self, transfer_id: UUID, from_status: TransferStatus, to_status: TransferStatus
    ) -> bool: ...

    # Для sweeper-а (SweepStaleTransfersUseCase): id переносов в одном из statuses,
    # у которых updated_at старше older_than.
    async def find_stale_ids(
        self, statuses: Sequence[TransferStatus], older_than: datetime
    ) -> list[UUID]: ...
