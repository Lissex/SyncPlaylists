from dataclasses import dataclass, field
from enum import StrEnum

from syncplaylists.shared_kernel.domain.base import ValueObject


class AccountStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"  # токен протух и не обновился — нужно переподключить
    DISCONNECTED = "disconnected"  # отключён пользователем, токены стёрты


@dataclass(frozen=True, slots=True)
class EncryptedToken(ValueObject):
    """Домен видит только шифротекст — расшифровка живёт в application (TokenCipher)."""

    ciphertext: bytes = field(repr=False)
