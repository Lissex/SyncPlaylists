import hashlib
import secrets
from enum import StrEnum
from typing import Final

# Код привязки читает и вводит человек: без похожих символов (0/O, 1/I/L), верхний
# регистр, 8 символов в виде XXXX-XXXX — ~40 бит, перебор закрывает rate limit и TTL.
_PAIRING_ALPHABET: Final = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PAIRING_CODE_LENGTH: Final = 8


def generate_pairing_code() -> str:
    raw = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_pairing_code(code: str) -> str:
    """Как ввёл человек → канонический вид: без пробелов и дефисов, верхний регистр."""
    cleaned = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{cleaned[:4]}-{cleaned[4:]}" if len(cleaned) == PAIRING_CODE_LENGTH else cleaned


def generate_device_token() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    """sha256 токена устройства или кода привязки: в БД и Redis сырые значения не пишем."""
    return hashlib.sha256(secret.encode()).hexdigest()


class SessionState(StrEnum):
    """Состояние площадки в браузере, о котором сообщает расширение."""

    OK = "ok"  # разрешение есть, вход выполнен — можно выполнять задачи
    LOGGED_OUT = "logged_out"
    CAPTCHA = "captcha"
    NO_PERMISSION = "no_permission"  # пользователь не дал (или отозвал) доступ к площадке
