class PairingNotFoundError(Exception):
    """Код привязки неверный, истёк или уже использован."""


class PlatformAccountConflictError(Exception):
    """На площадке у пользователя уже подключён другой аккаунт — сначала отключить его."""


class DeviceNotFoundError(Exception):
    """Устройства нет или оно принадлежит другому пользователю (не различаем)."""


class DeviceUnavailableError(Exception):
    """Операцию на конкретном устройстве (диагностика) выполнить нельзя: оно не на связи
    (offline) или не ответило вовремя (timeout)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
