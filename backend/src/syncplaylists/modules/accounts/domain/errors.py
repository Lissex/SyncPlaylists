class AccountAlreadyConnectedError(Exception):
    """На этой площадке у пользователя уже есть другой активный аккаунт."""


class AccountNotFoundError(Exception):
    """Аккаунта нет или он принадлежит другому пользователю (не различаем намеренно)."""


class AccountNotUsableError(Exception):
    """Аккаунт отключён или истёк — обращаться к площадке от его имени нельзя."""


class InvalidAccountTransitionError(Exception):
    pass


class InvalidPlatformTokenError(Exception):
    """Площадка не приняла токен при подключении аккаунта."""
