from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from syncplaylists.modules.transfers.domain.entities import Transfer, TransferItem
from syncplaylists.modules.transfers.domain.value_objects import TransferProgress, TransferStatus


@dataclass(frozen=True, slots=True)
class ProgressSample:
    """Лёгкий срез переноса для прогресса/ETA: шапка со счётчиками и время обработки
    последних items — без загрузки всех items (SSE опрашивает его раз в несколько секунд)."""

    user_id: UUID
    status: TransferStatus
    progress: TransferProgress
    recent_processed_at: tuple[datetime, ...]
    resume_at: datetime | None = None  # PAUSED_QUOTA: когда продолжим


class TransferRepository(Protocol):
    async def get(self, transfer_id: UUID) -> Transfer | None: ...

    # Шапка со счётчиками + processed_at последних `recent` items (новые первыми).
    async def progress_sample(self, transfer_id: UUID, recent: int) -> ProgressSample | None: ...

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

    # --- пауза по квоте площадки (PAUSED_QUOTA) — тоже условными UPDATE: во время
    # паузы ещё могут дописываться результаты run_match, полный save() их бы затёр ---

    # QUEUED|RUNNING|WRITING → PAUSED_QUOTA (paused_from = прежний статус), а если уже на
    # паузе — resume_at = max(старый, новый). Возвращает действующий resume_at; None —
    # перенос не в фазе, где паузу можно поставить (уже DONE/FAILED/REVIEW).
    async def pause_for_quota(self, transfer_id: UUID, resume_at: datetime) -> datetime | None: ...

    # PAUSED_QUOTA → paused_from, только если resume_at <= now. Возвращает фазу, в
    # которую вернулись; None — не на паузе или срок ещё не наступил.
    async def resume_from_quota(
        self, transfer_id: UUID, now: datetime
    ) -> TransferStatus | None: ...

    async def pending_positions(self, transfer_id: UUID) -> list[int]: ...

    # Для sweeper-а: переносы на паузе по квоте, срок которых прошёл до `due_before`
    # (задача resume_transfer потерялась).
    async def find_overdue_paused(self, due_before: datetime) -> list[UUID]: ...

    # Для sweeper-а (SweepStaleTransfersUseCase): id переносов в одном из statuses,
    # у которых updated_at старше older_than.
    async def find_stale_ids(
        self, statuses: Sequence[TransferStatus], older_than: datetime
    ) -> list[UUID]: ...
