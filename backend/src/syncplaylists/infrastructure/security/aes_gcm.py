import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION: Final = b"\x01"
_NONCE_BYTES: Final = 12
_KEY_BYTES: Final = 32


class TokenDecryptionError(Exception):
    """Шифротекст повреждён, подменён, привязан к другому AAD или зашифрован другим ключом."""


class AesGcmTokenCipher:
    """AES-256-GCM. Формат: версия(1) | nonce(12) | ciphertext+tag(16).

    Байт версии — задел под ротацию ключа (новая версия = новый ключ, старые
    шифротексты расшифровываются старым). AAD привязывает шифротекст к месту
    хранения (account_id + имя поля): нельзя переставить токен в другую строку или
    из refresh в access — расшифровка не пройдёт проверку тега.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_BYTES:
            raise ValueError(f"Ключ AES-GCM должен быть {_KEY_BYTES} байта, получено {len(key)}")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str, *, aad: bytes) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return _VERSION + nonce + self._aead.encrypt(nonce, plaintext.encode(), aad)

    def decrypt(self, ciphertext: bytes, *, aad: bytes) -> str:
        if len(ciphertext) <= 1 + _NONCE_BYTES or ciphertext[:1] != _VERSION:
            raise TokenDecryptionError("Неизвестный формат шифротекста")
        nonce = ciphertext[1 : 1 + _NONCE_BYTES]
        try:
            plaintext = self._aead.decrypt(nonce, ciphertext[1 + _NONCE_BYTES :], aad)
        except InvalidTag as exc:
            raise TokenDecryptionError("Шифротекст не прошёл проверку подлинности") from exc
        return plaintext.decode()
