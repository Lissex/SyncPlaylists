class InvalidEmailError(ValueError):
    pass


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    """Неверный email или пароль — намеренно без уточнения, что именно."""


class TooManyAttemptsError(Exception):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__(f"Слишком много попыток, повторите через {retry_after_s} с")
        self.retry_after_s = retry_after_s
