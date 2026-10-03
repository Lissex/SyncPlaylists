from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

# Сколько последних обработанных треков берём для оценки скорости.
ETA_WINDOW: Final = 20
_MIN_SAMPLES: Final = 2


def estimate_remaining(
    recent_processed_at: Sequence[datetime], pending: int, now: datetime
) -> timedelta | None:
    """Оценка оставшегося времени матчинга по скорости последних обработанных треков.

    Скорость = число треков в окне / время от первого из них до *сейчас* (а не до
    последнего обработанного): если площадка поставила перенос на паузу (429), треки
    перестают обрабатываться, и оценка честно растёт, а не замирает на старой скорости.
    None — данных пока мало (меньше двух обработанных треков)."""
    if pending <= 0:
        return timedelta(0)
    window = sorted(recent_processed_at)[-ETA_WINDOW:]
    if len(window) < _MIN_SAMPLES:
        return None
    elapsed = (now - window[0]).total_seconds()
    if elapsed <= 0:
        return None
    # После первого трека окна завершились ещё len(window) - 1 треков.
    per_track = elapsed / (len(window) - 1)
    return timedelta(seconds=per_track * pending)
