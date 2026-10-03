import base64
import hashlib
import os

import pytest
from pydantic import ValidationError

from syncplaylists.infrastructure.config.settings import CorsSettings, SecuritySettings
from syncplaylists.infrastructure.security.aes_gcm import AesGcmTokenCipher, TokenDecryptionError
from syncplaylists.integrations.platforms.fake.oauth import FakeOAuthProvider
from syncplaylists.modules.identity.infrastructure.passwords import Argon2PasswordHasher
from syncplaylists.modules.identity.presentation.cookies import SessionCookiePolicy
from syncplaylists.shared_kernel.domain.value_objects import Platform

_AAD = b"connected_account:1:access"


@pytest.fixture
def cipher() -> AesGcmTokenCipher:
    return AesGcmTokenCipher(os.urandom(32))


def test_aes_gcm_roundtrip(cipher: AesGcmTokenCipher) -> None:
    ciphertext = cipher.encrypt("vk-token-абв", aad=_AAD)

    assert b"vk-token" not in ciphertext
    assert cipher.decrypt(ciphertext, aad=_AAD) == "vk-token-абв"


def test_aes_gcm_uses_random_nonce(cipher: AesGcmTokenCipher) -> None:
    assert cipher.encrypt("same", aad=_AAD) != cipher.encrypt("same", aad=_AAD)


def test_aes_gcm_detects_tampering(cipher: AesGcmTokenCipher) -> None:
    ciphertext = bytearray(cipher.encrypt("token", aad=_AAD))
    ciphertext[-1] ^= 0x01

    with pytest.raises(TokenDecryptionError):
        cipher.decrypt(bytes(ciphertext), aad=_AAD)


def test_aes_gcm_rejects_foreign_aad(cipher: AesGcmTokenCipher) -> None:
    ciphertext = cipher.encrypt("token", aad=_AAD)

    with pytest.raises(TokenDecryptionError):
        cipher.decrypt(ciphertext, aad=b"connected_account:2:access")


def test_aes_gcm_rejects_other_key(cipher: AesGcmTokenCipher) -> None:
    ciphertext = cipher.encrypt("token", aad=_AAD)

    with pytest.raises(TokenDecryptionError):
        AesGcmTokenCipher(os.urandom(32)).decrypt(ciphertext, aad=_AAD)


def test_aes_gcm_rejects_unknown_format(cipher: AesGcmTokenCipher) -> None:
    with pytest.raises(TokenDecryptionError):
        cipher.decrypt(b"\x02" + os.urandom(40), aad=_AAD)
    with pytest.raises(TokenDecryptionError):
        cipher.decrypt(b"\x01short", aad=_AAD)


def test_aes_gcm_key_must_be_32_bytes() -> None:
    with pytest.raises(ValueError, match="32"):
        AesGcmTokenCipher(os.urandom(16))


def test_security_settings_validate_key() -> None:
    SecuritySettings(token_encryption_key=base64.b64encode(os.urandom(32)).decode())
    with pytest.raises(ValidationError):
        SecuritySettings(token_encryption_key=base64.b64encode(os.urandom(16)).decode())
    with pytest.raises(ValidationError):
        SecuritySettings(token_encryption_key="not base64!!")


def test_cors_settings_reject_wildcard() -> None:
    with pytest.raises(ValidationError):
        CorsSettings(allowed_origins=["*"])


def test_argon2_hash_and_verify() -> None:
    hasher = Argon2PasswordHasher()
    password_hash = hasher.hash("correct horse")

    assert password_hash.startswith("$argon2id$")
    assert hasher.verify(password_hash, "correct horse")
    assert not hasher.verify(password_hash, "wrong")
    assert not hasher.verify("garbage", "correct horse")
    assert not hasher.needs_rehash(password_hash)


def test_cookie_policy_uses_host_prefix_only_when_secure() -> None:
    assert SessionCookiePolicy(secure=True, max_age_seconds=60).name == "__Host-sp_session"
    assert SessionCookiePolicy(secure=False, max_age_seconds=60).name == "sp_session"


async def test_fake_oauth_provider_checks_pkce() -> None:
    provider = FakeOAuthProvider(Platform.SPOTIFY)
    verifier = "v" * 64
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    url = provider.authorization_url(
        state="s", code_challenge=challenge, redirect_uri="http://api/cb"
    )
    code = url.split("code=")[1].split("&")[0]

    grant = await provider.exchange_code(code=code, code_verifier=verifier, redirect_uri="x")
    assert grant.external_user_id == "fake-spotify-user"
    with pytest.raises(ValueError, match="PKCE"):
        await provider.exchange_code(code=code, code_verifier="w" * 64, redirect_uri="x")
