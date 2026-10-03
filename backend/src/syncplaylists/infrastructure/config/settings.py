import base64
import binascii

from pydantic import BaseModel, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from syncplaylists.shared_kernel.domain.value_objects import Platform

_AES_KEY_BYTES = 32


class DatabaseSettings(BaseModel):
    dsn: PostgresDsn
    pool_size: int = 10
    echo: bool = False


class RedisSettings(BaseModel):
    dsn: RedisDsn


class SecuritySettings(BaseModel):
    # base64 от 32 случайных байт — ключ AES-256-GCM для токенов площадок.
    token_encryption_key: SecretStr
    session_ttl_minutes: int = 60 * 24 * 7
    # True (прод) — cookie с флагом Secure и префиксом __Host-; в dev по http — False.
    cookie_secure: bool = True
    auth_rate_limit_attempts: int = 5
    auth_rate_limit_window_seconds: int = 60

    @field_validator("token_encryption_key")
    @classmethod
    def _key_is_32_bytes_base64(cls, value: SecretStr) -> SecretStr:
        try:
            raw = base64.b64decode(value.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("token_encryption_key должен быть в base64") from exc
        if len(raw) != _AES_KEY_BYTES:
            raise ValueError(f"token_encryption_key должен содержать {_AES_KEY_BYTES} байта")
        return value

    def token_encryption_key_bytes(self) -> bytes:
        return base64.b64decode(self.token_encryption_key.get_secret_value())


class CorsSettings(BaseModel):
    allowed_origins: list[str] = ["http://localhost:5173"]

    @field_validator("allowed_origins")
    @classmethod
    def _no_wildcard(cls, value: list[str]) -> list[str]:
        # С allow_credentials=True "*" небезопасен (и браузер его всё равно отвергнет) —
        # только явный allowlist.
        if "*" in value:
            raise ValueError("CORS allowed_origins не может содержать '*'")
        return value


class OAuthSettings(BaseModel):
    # Публичный адрес API, на который площадка возвращает пользователя:
    # {callback_base_url}/accounts/{platform}/oauth/callback
    callback_base_url: str = "http://localhost:8000"
    frontend_redirect_url: str = "http://localhost:5173/accounts"
    state_ttl_seconds: int = 600
    # Площадки, для которых регистрируется FakeOAuthProvider (dev/тесты) — пока
    # настоящих OAuth-клиентов нет (появятся вместе с адаптерами площадок).
    fake_platforms: list[Platform] = []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # ".env" — если команды запускаются из backend/ с собственным .env;
        # "../.env" — единый .env в корне репозитория (там же, где docker-compose.yml).
        env_file=(".env", "../.env"),
        env_nested_delimiter="__",
        extra="ignore",
    )

    env: str = "dev"
    db: DatabaseSettings
    redis: RedisSettings
    security: SecuritySettings
    cors: CorsSettings = CorsSettings()
    oauth: OAuthSettings = OAuthSettings()
