from typing import Any
from uuid import UUID

from dishka import FromDishka
from dishka.integrations.arq import inject

from syncplaylists.modules.extension.application.diagnostics import RunProbeUseCase


@inject
async def probe_extension(
    ctx: dict[str, Any],
    probe_id: str,
    user_id: UUID,
    device_id: UUID,
    nonce: str,
    use_case: FromDishka[RunProbeUseCase],
) -> None:
    """Проверка связи с расширением (диагностика, только dev). Очередь client."""
    await use_case.execute(probe_id, user_id, device_id, nonce)
