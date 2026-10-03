import base64
import hashlib
import secrets
from urllib.parse import urlencode

from syncplaylists.modules.accounts.application.ports import OAuthGrant
from syncplaylists.shared_kernel.domain.value_objects import Platform


class FakeOAuthProvider:
    """OAuth-провайдер без внешней площадки (dev/тесты): «страница входа» сразу
    возвращает пользователя на наш callback с кодом. Проверяет только сквозную механику
    start → callback → ConnectAccount; настоящие клиенты (Spotify/SoundCloud/Google)
    появятся вместе с адаптерами площадок."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        # Кодируем challenge в код, чтобы exchange_code мог проверить PKCE как настоящий
        # сервер авторизации.
        code = f"fake-{code_challenge}"
        return f"{redirect_uri}?{urlencode({'code': code, 'state': state})}"

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthGrant:
        # Та же PKCE-проверка, что у сервера авторизации: challenge = base64url(sha256(verifier)).
        expected = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        if code != f"fake-{expected.rstrip(b'=').decode()}":
            raise ValueError("PKCE: code_verifier не соответствует code_challenge")
        return OAuthGrant(
            external_user_id=f"fake-{self.platform.value}-user",
            display_name=f"Fake {self.platform.value} user",
            access_token=secrets.token_urlsafe(24),
            refresh_token=secrets.token_urlsafe(24),
            expires_at=None,
        )
