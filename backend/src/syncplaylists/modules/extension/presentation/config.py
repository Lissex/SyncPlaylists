from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ExtensionEndpointConfig:
    """Параметры WebSocket расширения (собираются из Settings в bootstrap)."""

    # chrome-extension://<id> — id опубликованных сборок (Chrome, Яндекс Браузер). У
    # Firefox UUID moz-extension:// случаен на каждую установку — его по id не проверить,
    # принимаем по схеме. Главная защита — токен устройства; Origin отсекает обычные
    # веб-страницы (cross-site WebSocket).
    allowed_extension_ids: Sequence[str] = field(default_factory=tuple)
    # Пустой allowlist в dev — любое chrome-extension:// (распакованная сборка).
    allow_any_chrome_extension: bool = False
    heartbeat_seconds: int = 20
    presence_ttl_seconds: int = 60
    hello_timeout_seconds: float = 10.0
    max_message_bytes: int = 2_000_000

    def origin_allowed(self, origin: str | None) -> bool:
        if origin is None:
            # Не браузер (браузер Origin шлёт всегда) — Origin здесь ничего не доказывает,
            # решает токен.
            return True
        if origin.startswith("moz-extension://"):
            return True
        if origin.startswith("chrome-extension://"):
            extension_id = origin.removeprefix("chrome-extension://").rstrip("/")
            return self.allow_any_chrome_extension or extension_id in self.allowed_extension_ids
        return False
