class PairingNotFoundError(Exception):
    """Код привязки неверный, истёк или уже использован."""


class PlatformAccountConflictError(Exception):
    """На площадке у пользователя уже подключён другой аккаунт — сначала отключить его."""


class DeviceNotFoundError(Exception):
    """Устройства нет или оно принадлежит другому пользователю (не различаем)."""
