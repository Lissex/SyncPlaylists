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


class RateLimitSettings(BaseModel):
    """Token bucket на (площадка, аккаунт): capacity — допустимый всплеск запросов,
    refill_per_second — устойчивая частота. Если ждать токен дольше max_wait_seconds,
    задача уходит в повтор с задержкой, а не держит воркер."""

    # Общие значения по умолчанию; у каждой площадки свои (YandexSettings, SoundCloudSettings).
    capacity: int = 3
    refill_per_second: float = 1.5
    max_wait_seconds: float = 10.0


class YandexSettings(BaseModel):
    # 429 Яндекса — антибот (x-yandex-captcha), а не квота API: срабатывал на 4–6-м
    # запросе при старте с 3 одновременных, а по одному в секунду зонд сделал 150 поисков
    # подряд без капчи (ARCHITECTURE.md, 11g). Поэтому без всплеска и не чаще 1/с.
    rate_limit: RateLimitSettings = RateLimitSettings(capacity=1, refill_per_second=1.0)
    # Общий лимит на весь сервер (все аккаунты и процессы): антибот видит суммарный
    # трафик с IP. ~1 rps → потолок ~3600 поисков/час на сервер (ARCHITECTURE.md, 11g).
    # None — без общего лимита.
    global_rate_limit: RateLimitSettings | None = RateLimitSettings(
        capacity=1, refill_per_second=1.0
    )
    request_timeout_seconds: float = 15.0
    # Сколько треков догружать одним запросом /tracks и добавлять одной пачкой.
    batch_size: int = 100


class SoundCloudOfficialSettings(BaseModel):
    """Приложение в официальном API SoundCloud (нужен Artist Pro у разработчика). Пока
    не задано — OAuth-подключение SoundCloud выключено, работает только v2 по токену."""

    client_id: str
    client_secret: SecretStr


class SoundCloudSettings(BaseModel):
    # Консервативно: фактический лимит v2 неизвестен, подбирается по логам 429.
    rate_limit: RateLimitSettings = RateLimitSettings(capacity=3, refill_per_second=1.0)
    request_timeout_seconds: float = 15.0
    # /tracks?ids= принимает до 50 id за запрос.
    tracks_batch_size: int = 50
    likes_page_size: int = 200
    # Больше 500 треков SoundCloud в один плейлист не принимает.
    playlist_max_tracks: int = 500
    # client_id веб-клиента извлекается из JS сайта и кэшируется в Redis.
    client_id_ttl_seconds: int = 86400
    # Не чаще: иначе протухший токен пользователя (тот же 401) скачивал бы сайт на
    # каждый запрос.
    client_id_min_refresh_seconds: int = 60
    # Ручной client_id — если сайт поменяет разметку и извлечение сломается.
    client_id_override: str | None = None
    official: SoundCloudOfficialSettings | None = None

    @field_validator("client_id_override", mode="before")
    @classmethod
    def _blank_override_is_none(cls, value: object) -> object:
        # docker-compose передаёт незаданную переменную пустой строкой.
        return value or None

    @field_validator("official", mode="before")
    @classmethod
    def _blank_official_is_none(cls, value: object) -> object:
        if isinstance(value, dict) and not value.get("client_id"):
            return None  # пустые OFFICIAL__* из compose — официальный API выключен
        return value


class PlatformsSettings(BaseModel):
    # Площадки, которые обслуживает in-memory фейк (dev/тесты) — вместо настоящего
    # адаптера, если он есть. Настоящие адаптеры — у Яндекса и SoundCloud.
    fake: list[Platform] = []
    yandex: YandexSettings = YandexSettings()
    soundcloud: SoundCloudSettings = SoundCloudSettings()
    # Раскрытие коротких ссылок (vk.cc, on.soundcloud.com, ...).
    link_expander_timeout_seconds: float = 5.0
    # Кэш результатов поиска площадок в Redis (экономия квоты); 0 — выключен.
    search_cache_ttl_seconds: int = 86400


class ExtensionSettings(BaseModel):
    """Браузерное расширение (этап 4c)."""

    # chrome-extension://<id> опубликованных сборок (Chrome Web Store, Яндекс Браузер).
    # Пусто — в dev принимается любая распакованная сборка; в проде задать обязательно.
    allowed_extension_ids: list[str] = []
    pairing_code_ttl_seconds: int = 300
    pairing_poll_interval_seconds: int = 3
    # Привязка: попыток на IP (выдача кода) и на пользователя (ввод кода) в окне.
    pairing_rate_limit_attempts: int = 10
    pairing_rate_limit_window_seconds: int = 60
    device_token_ttl_days: int = 180
    heartbeat_seconds: int = 20
    # Расширение без heartbeat дольше этого считается отключённым (браузер закрыт).
    presence_ttl_seconds: int = 60
    # Площадки, которые через расширение обслуживает общий шлюз (все операции — в
    # браузере). dev/тесты: сквозной тест с фейковым расширением.
    generic_platforms: list[Platform] = []

    @field_validator("allowed_extension_ids", mode="before")
    @classmethod
    def _blank_is_empty(cls, value: object) -> object:
        return value or []


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
    platforms: PlatformsSettings = PlatformsSettings()
    extension: ExtensionSettings = ExtensionSettings()
