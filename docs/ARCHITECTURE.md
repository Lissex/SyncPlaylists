# SyncPlaylists — архитектура

Веб-сервис переноса плейлистов между **Spotify, Яндекс Музыкой, VK Музыкой, SoundCloud и YouTube Music**,
с сопоставлением треков по ISRC, по тексту и по звуку (аудио-распознавание).

> Статус: проектирование. Документ — источник правды для архитектурных решений.
> Меняешь решение — обнови этот файл.

---

## 0. Что делает сервис (функциональные требования)

### Сценарий переноса
1. **Источник** — одно из:
   - ссылка на плейлист любой площадки (включая короткие ссылки: `on.soundcloud.com`, `vk.cc`, …);
   - **медиатека пользователя** («Любимые треки» Spotify, «Мне нравится» Яндекса,
     «Моя музыка» VK, Likes SoundCloud, «Понравившиеся» YouTube Music).
2. **Назначение** — одно из:
   - существующий плейлист (по ссылке);
   - **новый плейлист**, создаётся автоматически (название и описание по умолчанию берутся из источника);
   - **медиатека** пользователя на целевой площадке.
3. Сервис читает треки источника → сопоставляет каждый с целевой площадкой → показывает результат →
   записывает в назначение.
4. **Если по тексту трек не найден или найден неуверенно — обязательно запускается распознавание по звуку**
   (если источник отдаёт аудио). Только после этого трек попадает в «не найдено» / ручной выбор.

### Правила записи
- **Порядок сохраняется.** Для медиатек, куда новое добавляется наверх (VK, Яндекс), пишем в обратном порядке,
  чтобы самый свежий лайк оказался сверху.
- **Без дублей.** Треки, которые уже есть в назначении, не добавляются повторно.
- **Возобновляемость.** Перенос медиатеки на тысячи треков может идти часами; при сбое или капче
  продолжается с места остановки.
- **Отчёт** в конце: перенесено / найдено по звуку / подтверждено вручную / не найдено (с причиной).

### Обогащение треков: тексты и обложки
- **Тексты песен.** Основной источник — **Genius**. Официальный API Genius отдаёт метаданные и ссылку на страницу
  с текстом, но **не сам текст**. Получить текст можно только парсингом страницы, а это против ToS Genius,
  и сам текст защищён авторским правом.
  Поэтому делаем цепочку провайдеров за портом `LyricsProvider`, включаемых флагами:
  1. Genius API — метаданные, ссылка «Текст на Genius» (легально, всегда);
  2. LRCLIB — бесплатная открытая база, в том числе синхронизированные тексты (LRC);
  3. платный лицензированный провайдер (Musixmatch / LyricFind), если сервис станет публичным и коммерческим.
  ⚠️ Решение, показывать ли полный текст в публичном сервисе, — юридическое, принимается до релиза.
  Полные тексты в БД надолго не храним, кэш с TTL.
- **Оригинальные обложки** (порт `ArtworkProvider`), цепочка по качеству:
  1. **Deezer API** по ISRC (`/track/isrc:XXXX`) — бесплатно, без ключа, до 1000×1000;
  2. **iTunes Search API** (Apple) — бесплатно, без ключа, URL масштабируется до 3000×3000;
  3. **Cover Art Archive** (MusicBrainz) — открытые данные;
  4. Genius `song_art_image_url`;
  5. обложка самой площадки-источника.
  Храним URL + доминирующий цвет (для UI), сами картинки — только в кэше объектного хранилища.
- **Бонус для матчинга:** Deezer и iTunes отдают метаданные бесплатно, поэтому работают как «мост»:
  трек из VK/Яндекса без ISRC → нашли на Deezer → получили ISRC → точный поиск в Spotify.

### Чистка медиатеки
- поиск дублей (один `CanonicalTrack` — разные версии/перезаливы), с выбором, что оставить;
- объединение нескольких плейлистов в один без дублей;
- сравнение площадок: «есть в VK, но нет в Яндексе» → можно сразу перенести разницу;
- поиск недоступных/изъятых треков и предложение замены.
Любые удаления — только после подтверждения пользователем, с отчётом и возможностью отката из бэкапа.

### Бэкап и импорт медиатеки
- Экспорт плейлиста или всей медиатеки в форматы: **JSON** (полный, для обратного импорта),
  **CSV**, **XLSX**, **M3U8** (`#EXTINF` с длительностью), **XSPF**, **TXT** («Артист — Трек»).
  Опционально в архив добавляются обложки.
- Бэкап **вручную** или **по расписанию** (ARQ cron); файлы — в S3-совместимом хранилище
  (SeaweedFS локально), отдаются по временной ссылке.
- **Импорт из файла** любого из этих форматов — ещё один вид источника (`FileSource`) для переноса.

### Поддерживаемые источники/назначения
| Площадка | Плейлист по ссылке | Медиатека (чтение) | Медиатека (запись) |
|---|---|---|---|
| Spotify | ✅ | ✅ (OAuth/расширение) | ✅ (OAuth/расширение) |
| Яндекс | ✅ | ✅ | ✅ |
| VK | ✅ | ✅ | ✅ |
| SoundCloud | ✅ | ✅ | ✅ |
| YouTube Music | ✅ | ✅ | ✅ |

---

## 1. Стек одним взглядом

| Слой | Технологии |
|---|---|
| Язык бэкенда | **Python 3.12+** |
| Веб | **FastAPI**, Pydantic v2 |
| Настройки | **pydantic-settings** (`.env`, вложенные модели, `SecretStr`) |
| DI | **dishka** (async, интеграции с FastAPI и ARQ) |
| БД | **PostgreSQL 17** + `pg_trgm`, `unaccent`, `jsonb` |
| ORM / миграции | **SQLAlchemy 2.0 async + asyncpg**, **Alembic** |
| Очереди | **Redis 7 + ARQ** |
| Реалтайм | Redis pub/sub → **SSE** (`EventSource` на фронте) |
| HTTP / OAuth | **httpx**, **Authlib** |
| Матчинг | **rapidfuzz**, транслитерация (кириллица ↔ латиница) |
| Аудио | **ffmpeg**, **shazamio**, **ACRCloud** (fallback), **chromaprint / pyacoustid** (проверка) |
| Обогащение | **Genius API**, **LRCLIB**, **Deezer API**, **iTunes Search API**, **Cover Art Archive** |
| Файлы | **SeaweedFS** (dev) / любой S3 (aioboto3), экспорт: `openpyxl` (XLSX), stdlib (CSV/JSON/M3U8/XSPF) |
| Площадки | `spotipy`/свой клиент, `yandex-music`, `vkpymusic`, `ytmusicapi`, `soundcloud-v2` / официальный API |
| Качество | **uv**, **ruff**, **mypy --strict**, **pytest**, pytest-asyncio, **respx**, **testcontainers**, **import-linter**, pre-commit |
| Наблюдаемость | **structlog**, **Sentry** |
| Фронтенд | **React 19 + Vite + TS**, TanStack Query/Router, Tailwind + shadcn/ui, **Feature-Sliced Design** |
| Расширение | **WXT + TypeScript**, Manifest V3 (Chrome + Firefox) |
| Инфра | **Docker Compose** (+ SeaweedFS S3), Caddy (HTTPS); прод: VPS в ЕС + воркер в РФ через WireGuard/Tailscale |

---

## 2. Архитектурные принципы

1. **Clean Architecture** — зависимости направлены только внутрь:
   `presentation → application → domain`, `infrastructure → application/domain`.
   Домен не знает ни про FastAPI, ни про SQLAlchemy, ни про Redis, ни про настройки.
2. **DDD** — код разбит по **ограниченным контекстам** (bounded contexts), внутри — агрегаты,
   value objects, доменные сервисы, доменные события, порты (интерфейсы репозиториев и шлюзов).
3. **Модульный монолит** — один деплой, но контексты изолированы так, чтобы любой
   (например, `recognition`) можно было вынести в отдельный сервис без переписывания.
4. **Порты и адаптеры** — каждая площадка, распознаватель, очередь, шифрование — это
   реализация интерфейса (`Protocol`), объявленного во внутреннем слое.
5. **Границы проверяются автоматически** — `import-linter` падает в CI, если слой
   импортирует то, что ему нельзя.

---

## 3. Ограниченные контексты

| Контекст | Ответственность | Ключевые сущности |
|---|---|---|
| `identity` | пользователи, вход, сессии | `User` |
| `accounts` | подключённые аккаунты площадок, токены, транспорт | `ConnectedAccount` |
| `catalog` | треки площадок и канонические треки | `PlatformTrack`, `CanonicalTrack` |
| `matching` | нормализация, скоринг, пайплайн стратегий, кэш соответствий | `TrackMatch`, `MatchingPipeline` |
| `recognition` | получение аудио-фрагмента, распознавание, отпечатки | `Recognition`, `Fingerprint` |
| `transfers` | перенос плейлиста, ревью, прогресс | **`Transfer`** (агрегат) + `TransferItem` |
| `enrichment` | тексты, обложки, метаданные (ISRC-мост через Deezer/iTunes) | `TrackLyrics`, `Artwork` |
| `library_tools` | чистка: дубли, слияние, сравнение площадок | `CleanupReport`, `DuplicateGroup` |
| `backups` | экспорт/импорт, расписания бэкапов | `Backup`, `BackupSchedule` |

Общий словарь (`shared_kernel`): `Platform`, `ISRC`, `Duration`, `ExternalTrackRef`,
`PlaylistRef`, базовые `Entity`, `AggregateRoot`, `DomainEvent`, `ValueObject`.

**Правило общения контекстов:** только через публичный application-интерфейс контекста
или через доменные события. Импортировать `domain` чужого контекста напрямую нельзя.

---

## 4. Структура репозитория

```
SyncPlaylists/
├─ CLAUDE.md
├─ docs/ARCHITECTURE.md
├─ docker-compose.yml
├─ .env.example
├─ backend/
│  ├─ pyproject.toml                # uv, ruff, mypy, pytest, import-linter
│  ├─ alembic.ini
│  ├─ migrations/
│  ├─ src/syncplaylists/
│  │  ├─ shared_kernel/
│  │  │  ├─ domain/                 # Platform, ISRC, Duration, Entity, AggregateRoot, DomainEvent
│  │  │  └─ application/            # UnitOfWork, EventPublisher, Clock — порты
│  │  ├─ modules/
│  │  │  ├─ transfers/
│  │  │  │  ├─ domain/              # Transfer, TransferItem, статусы, события, TransferRepository(Protocol)
│  │  │  │  ├─ application/         # commands/, queries/, dto.py, ports.py
│  │  │  │  ├─ infrastructure/      # orm.py, mappers.py, repository.py
│  │  │  │  └─ presentation/        # api.py (FastAPI router), schemas.py, tasks.py (ARQ)
│  │  │  ├─ matching/               # ...та же структура
│  │  │  ├─ catalog/
│  │  │  ├─ recognition/
│  │  │  ├─ accounts/
│  │  │  └─ identity/
│  │  ├─ integrations/              # адаптеры внешних систем (реализуют порты модулей)
│  │  │  ├─ platforms/
│  │  │  │  ├─ base.py              # общие хелперы, rate limiter
│  │  │  │  ├─ spotify/  yandex/  vk/  soundcloud/  ytmusic/
│  │  │  │  └─ extension/           # шлюз «через браузерное расширение»
│  │  │  └─ recognizers/            # shazam.py, acrcloud.py, chromaprint.py, ffmpeg.py
│  │  ├─ infrastructure/            # общая инфраструктура
│  │  │  ├─ config/settings.py      # pydantic-settings
│  │  │  ├─ db/                     # engine, session, base ORM, UnitOfWork
│  │  │  ├─ queue/                  # ARQ pool, enqueue-адаптер
│  │  │  ├─ events/                 # Redis pub/sub publisher
│  │  │  ├─ security/               # AES-GCM TokenCipher
│  │  │  └─ ratelimit/              # token bucket на Redis
│  │  └─ bootstrap/
│  │     ├─ container.py            # dishka providers (composition root)
│  │     ├─ api.py                  # create_app()
│  │     └─ worker.py               # ARQ WorkerSettings
│  └─ tests/
│     ├─ unit/                      # домен + application на фейках, без БД и сети
│     ├─ integration/               # Postgres/Redis в testcontainers, адаптеры через respx
│     └─ fixtures/dirty_titles.json # реальные «грязные» названия VK/SoundCloud
├─ frontend/                        # React + Vite, Feature-Sliced Design
│  └─ src/{app,pages,widgets,features,entities,shared}/
└─ extension/                       # WXT
   └─ entrypoints/{background.ts,content/}  +  platforms/{spotify,vk,yandex}.ts
```

---

## 5. Слои: кто что делает

| Слой | Что лежит | Может зависеть от | Пример |
|---|---|---|---|
| **domain** | сущности, агрегаты, VO, доменные сервисы, события, интерфейсы репозиториев | только `shared_kernel.domain`, stdlib | `Transfer.record_match(...)`, `MatchScorer` |
| **application** | use cases (commands/queries), DTO, порты внешних систем | domain | `StartTransfer`, `ResolveUncertainItem`, `MusicPlatformGateway` |
| **infrastructure** | ORM-модели, мапперы, репозитории, клиенты API, Redis, ARQ, шифрование, settings | application, domain | `SqlTransferRepository`, `YandexGateway` |
| **presentation** | FastAPI-роутеры, Pydantic-схемы запросов/ответов, ARQ-таски, SSE | application | `POST /transfers`, `run_transfer_task` |
| **bootstrap** | сборка всего через DI | всё | `make_container(settings)` |

ORM-модели **отделены** от доменных сущностей; преобразование — в `mappers.py`.
Домен остаётся чистым Python и тестируется без БД.

---

## 6. Настройки (pydantic-settings)

```python
# infrastructure/config/settings.py
from pydantic import BaseModel, SecretStr, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

class DatabaseSettings(BaseModel):
    dsn: PostgresDsn
    pool_size: int = 10
    echo: bool = False

class RedisSettings(BaseModel):
    dsn: RedisDsn

class SecuritySettings(BaseModel):         # реализовано на этапе 4a
    token_encryption_key: SecretStr      # AES-256-GCM ключ: base64 от 32 байт (валидируется)
    session_ttl_minutes: int = 60 * 24 * 7
    cookie_secure: bool = True           # True → Secure + имя cookie с префиксом __Host-
    auth_rate_limit_attempts: int = 5    # /auth/login и /auth/register, на (IP, email)
    auth_rate_limit_window_seconds: int = 60

class CorsSettings(BaseModel):           # этап 4a; "*" запрещён валидатором
    allowed_origins: list[str] = ["http://localhost:5173"]

class OAuthSettings(BaseModel):          # этап 4a
    callback_base_url: str = "http://localhost:8000"
    frontend_redirect_url: str = "http://localhost:5173/accounts"
    state_ttl_seconds: int = 600
    fake_platforms: list[Platform] = []  # FakeOAuthProvider для dev/тестов

class SpotifySettings(BaseModel):
    client_id: str
    client_secret: SecretStr
    redirect_uri: str

class SoundCloudOfficialSettings(BaseModel):   # этап 4b-2: приложение официального API
    client_id: str
    client_secret: SecretStr

class SoundCloudSettings(BaseModel):     # этап 4b-2
    rate_limit: RateLimitSettings = RateLimitSettings(capacity=3, refill_per_second=1.0)
    request_timeout_seconds: float = 15.0
    tracks_batch_size: int = 50          # /tracks?ids= — до 50 id
    likes_page_size: int = 200
    playlist_max_tracks: int = 500       # больше SoundCloud в сет не принимает
    client_id_ttl_seconds: int = 86400   # client_id веб-клиента в Redis
    client_id_min_refresh_seconds: int = 60
    client_id_override: str | None = None
    official: SoundCloudOfficialSettings | None = None   # None — OAuth SoundCloud выключен

class RateLimitSettings(BaseModel):      # этап 4b: token bucket на (площадка, аккаунт)
    capacity: int = 3                    # под Яндекс, подобрано по e2e (11d)
    refill_per_second: float = 1.5
    max_wait_seconds: float = 10.0       # дольше — PlatformRateLimitedError → повтор задачи

class YandexSettings(BaseModel):         # этап 4b; секретов нет — токены лежат в БД
    rate_limit: RateLimitSettings = RateLimitSettings()
    request_timeout_seconds: float = 15.0
    batch_size: int = 100                # догрузка /tracks и пачки записи

class PlatformsSettings(BaseModel):      # этап 4b
    fake: list[Platform] = []            # площадки на in-memory фейке (dev/тесты)
    yandex: YandexSettings = YandexSettings()
    soundcloud: SoundCloudSettings = SoundCloudSettings()   # 4b-2
    link_expander_timeout_seconds: float = 5.0

class RecognitionSettings(BaseModel):
    shazam_enabled: bool = True
    acrcloud_host: str | None = None
    acrcloud_key: SecretStr | None = None
    acrcloud_secret: SecretStr | None = None
    fragment_seconds: int = 18

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",       # DATABASE__DSN=..., SPOTIFY__CLIENT_ID=...
        extra="ignore",
    )
    env: str = "dev"
    db: DatabaseSettings
    redis: RedisSettings
    security: SecuritySettings
    cors: CorsSettings = CorsSettings()
    oauth: OAuthSettings = OAuthSettings()
    platforms: PlatformsSettings = PlatformsSettings()
    spotify: SpotifySettings
    recognition: RecognitionSettings = RecognitionSettings()
```

- Settings создаются **один раз** в `bootstrap` и раздаются через DI.
- Домен и application **не импортируют** settings; если use case нужен параметр
  (например, порог скоринга) — он передаётся в конструктор как обычное значение.
- Секреты — только `SecretStr`, в логах не светятся. В репозитории — только `.env.example`.
- **Поля добавляются поэтапно**, а не все сразу по примеру выше: на этапе 1 (скелет) в `Settings`
  только `env`, `db`, `redis` — ровно то, что нужно FastAPI/Alembic/ARQ-скелету. `security`,
  `spotify`, `soundcloud`, `recognition` и т.д. добавляются вместе с контекстом, которому они
  нужны (`accounts`, адаптеры площадок, `recognition`), чтобы `.env.example` не требовал секретов
  раньше, чем появится код, который их использует.

---

## 7. Доменная модель

### Value objects (`shared_kernel`)
- `Platform` — `SPOTIFY | YANDEX | VK | SOUNDCLOUD | YTMUSIC`
- `ISRC` — валидируется (`^[A-Z]{2}[A-Z0-9]{3}\d{7}$`)
- `Duration` — миллисекунды, `is_close_to(other, tolerance_ms=3000)`
- `ExternalTrackRef(platform, external_id)`, `PlaylistRef(platform, external_id)`
- `MatchScore` — float 0..1 с порогами `AUTO ≥ 0.90`, `UNCERTAIN ≥ 0.70`
- `TrackQuery`, `TrackCandidate` — DTO сигнатуры `MusicPlatformGateway` (см. раздел 8); лежат в
  `shared_kernel`, а не в контексте, который их первым использует (`matching`), потому что `catalog`
  и `transfers` будут использовать их на этапах 3-4 — см. правило размещения портов в разделе 8.

### Источник и назначение (value objects)
```
TrackSource       = PlaylistSource(ref: PlaylistRef)
                  | LibrarySource(platform, account_id)
                  | FileSource(file_id, format)          # импорт из бэкапа / CSV / M3U / TXT

TrackDestination  = ExistingPlaylist(ref: PlaylistRef)
                  | NewPlaylist(platform, title, description | None)
                  | LibraryDestination(platform, account_id)
```
Ссылки разбирает доменный сервис **`LinkResolver`** (набор парсеров по площадкам, короткие ссылки
раскрываются через порт `UrlExpander`) → `PlaylistRef`.

**Реализовано на этапе 4b** (`shared_kernel/domain/links.py`): `LinkResolver.resolve(raw)` →
`PlaylistLink(ref) | LibraryLink(platform)`. Лежит в `shared_kernel`, потому что ссылки нужны
и transfers, и (дальше) backups/library_tools; порт `UrlExpander` — в `shared_kernel/domain/ports.py`,
т.к. резолвер (домен) зовёт его сам. Парсеры — чистые regex + `urllib.parse`:

| Площадка | Форматы | `external_id` |
|---|---|---|
| Яндекс | `music.yandex.{ru,com,by,kz,uz}`, `next.music.yandex.ru`: `/users/<login>/playlists/<kind>`, `/playlists/<uuid>` | `"<login>:<kind>"` или uuid (без префикса `lk.` — с ним API отвечает 404) |
| Spotify | `open.spotify.com/[intl-xx/][user/x/]playlist/<id22>`, `spotify:playlist:<id>`; `/collection/tracks` → медиатека | id |
| VK | `vk.{com,ru}`, `m.vk.*`: `/music/playlist|album/…`, `/audio_playlist…`, `?z=audio_playlist…`, `?act=audio_playlist…&access_hash=` | `"<owner>_<id>[_<hash>]"` |
| SoundCloud | `[m.]soundcloud.com/<user>/sets/<slug>[/s-<secret>]`; `/you/likes` → медиатека; `/<user>/likes` | путь |
| YT Music | `music.youtube.com`, `youtube.com`, `youtu.be`: `list=`, `/browse/VL…`; `LM` → медиатека; `RD…` (миксы) — отказ | id без `VL` |

Резолвер извлекает ссылку из текста «Поделиться», отбрасывает трекинговые параметры, отвергает
альбомы/треки (`UnsupportedLinkError(reason)` с машиночитаемой причиной). Короткие ссылки
(`vk.cc`, `on.soundcloud.com`, `spotify.link`, `spoti.fi`, `soundcloud.app.goo.gl`) раскрывает
`HttpxUrlExpander` (`infrastructure/http`): **запросы только к хостам-сокращателям из allowlist**,
редиректы вручную, ≤ 5 переходов; как только `Location` ведёт на любой другой хост — остановка
без запроса туда (защита от SSRF), дальше решает парсер.

Поддерживается ли площадка адаптером и не «своя» ли это медиатека, решает application:
`transfers.application.links.ResolvePlaylistLinkUseCase`. Ссылка `users/<login>/playlists/3`
(«Мне нравится» Яндекса) становится медиатекой, только если `gateway.is_own_library(ref)` —
login/uid совпадает с подключённым аккаунтом пользователя; чужая — обычный плейлист.

### Агрегат `Transfer`
```
Transfer (root)
 ├─ id, user_id, source: TrackSource, destination: TrackDestination, resolved_targets: tuple[PlaylistRef]
 ├─ status: QUEUED → RUNNING → (PAUSED_CAPTCHA ↔ RUNNING) → REVIEW → WRITING → DONE | FAILED
 │          QUEUED | RUNNING | WRITING ↔ PAUSED_QUOTA (квота площадки, до resume_at; 4b-3)
 ├─ resume_at, paused_from (только в PAUSED_QUOTA)
 └─ items: list[TransferItem]
       └─ position, source_track: ExternalTrackRef, match: MatchResult | None,
          status: PENDING | MATCHED | UNCERTAIN | NOT_FOUND | ADDED | FAILED, candidates
```
Методы с инвариантами: `start()`, `record_match(position, result)`, `pause_for_captcha(...)`,
`resume()`, `resolve_item(position, chosen)`, `begin_writing()`, `mark_added(...)`, `complete()`, `fail(reason)`.
Каждый метод генерирует доменное событие.

### Доменные события
`TransferStarted`, `TrackMatched`, `TrackNeedsReview`, `TrackNotFound`, `CaptchaRequired`,
`TransferWritingStarted`, `TransferCompleted`, `TransferFailed`
→ после коммита UnitOfWork публикуются в Redis → SSE клиенту.

### Matching (доменные сервисы)
- `TrackNormalizer` — NFKC, lower, ё→е, вырезание `feat./ft./remaster/(Free DL)/[prod. ...]`,
  разбор `Artist - Title`, транслитерация.
- `MatchScorer` — `title×0.5 + artist×0.35 + duration×0.15` (rapidfuzz `token_set_ratio`).
- `MatchingPipeline` — цепочка стратегий (паттерн Chain of Responsibility):

```
SamePlatformStrategy → CacheStrategy → IsrcStrategy → FuzzySearchStrategy → AudioRecognitionStrategy → FingerprintVerification
      ↓ нет               ↓ нет             ↓ <0.70                    ↓ нет                    ↓
                                                                                    UNCERTAIN / NOT_FOUND → ручной выбор
```

`TrackNormalizer` дополнительно извлекает `VersionTag` трека (`original | live | remix(remixer) |
acoustic | sped_up | slowed | cover | instrumental | karaoke | extended | radio_edit`) из названия;
`MatchScorer` ограничивает результат потолком `UNCERTAIN`, если версии источника и кандидата не
совпадают (разный тег или разные ремиксеры) — даже при идеальном совпадении текста и длительности.

**Одна площадка — без поиска (этап 4b-1.1).** Первой в цепочке стоит `SamePlatformStrategy`:
если площадка источника совпадает с целевой (чужой плейлист к себе, лайки в плейлист, слияние),
трек и есть своё соответствие — MATCHED, `method=same_platform`, score 1.0, ноль запросов к
площадке. В `track_matches` такое соответствие не пишется (как и попадание в кэш).

**DJ-версии (этап 4b-2, по реальным названиям SoundCloud).** `(Ed Marquis Bootleg)`,
`[No Romeo Schranz Edit]`, `(X Rmx)`, `(X Flip)`, `(X Rework)`, `Song - KAAI Edit`, а также
`(Bootleg)`/`(VIP)`/`(Mashup)` без имени — это `remix(<ремиксер>)`, а не оригинал. Жанровое
слово перед ним (`hardstyle`, `schranz`, ...) в имя ремиксера не входит. Служебные правки
площадок (`radio`/`extended`/`clean`/`explicit edit`, голое `(Edit)`) ремиксом не считаются.
Мусор загрузчиков (`[FREE DL]`, `free download`, `BUY = …`, `OUT NOW …`, префиксы
`Premiere:`/`GTG Premiere |`, `Official Audio` без скобок, `[HQ]`, каталожные номера
`[MR047]`, хэштеги, `prod.by`) вырезается, после чего убираются пустые скобки и висящие по
краям разделители. Иначе ведущее `FREE DL |` ломало разбор «Artist - Title».

**Официальная заливка vs перезалив (этап 4b-2).** У площадок с пользовательскими заливками
(SoundCloud) один трек часто лежит и у артиста/лейбла, и у фан-страниц с тем же названием и
длительностью. `TrackCandidate` несёт `uploader` и `rights_holder` (есть `publisher_metadata`
с артистом/ISRC или `user.verified`). `matching.domain.UploadTrust`: официальная заливка
(`rights_holder` или ник заливщика ≈ артист источника, в т.ч. «JuiceWRLDofficial», «kinoband»)
получает `+0.03`, и скорер сравнивает артиста по артисту источника. Перезалив
(`uploader` ≠ артист) получает `−0.05`. При баллах ближе `0.02` побеждает официальная.
Бонус не поднимает несовпадающую версию выше её потолка. Единственный перезалив по-прежнему
находится.

**Поисковый запрос — из разобранного источника (этап 4b-3, по e2e SoundCloud → Яндекс).**
`FuzzySearchStrategy` строит запрос из `TrackNormalizer.normalize()`, а не из сырых полей:
артист — из «Artist – Title» в названии (а не ник заливщика «Finesse Music»), без мусора
загрузчиков (`(Prod Me)`, `prod. …`), feat-артисты — к артистам. Версия идёт **сразу в
единственный запрос**: очищенное название + `version_search_suffix` (`live`, `<remixer> remix`,
`acoustic`, `sped up`, `slowed`, `instrumental`, `extended mix`, `radio edit`; для
cover/karaoke суффикса нет). Отдельного второго поиска «той же версии» (4b) больше нет: он стоил
лишнего запроса к квоте площадки на каждый трек с версией. Кандидат той же версии с AUTO →
MATCHED; иначе лучший — оригинал под потолком скорера → `UNCERTAIN` на ручное подтверждение.

**feat-артисты.** «(feat. X)», «[ft. X]», «A feat. B - Title» — X тоже артист трека: перед
вырезанием из названия добавляется к артистам (без повторов). Иначе «ANIKV, SALUKI» на
площадке совпадал с источником «ANIKV» наполовину и точное совпадение уходило в UNCERTAIN.

---

## 8. Порты (интерфейсы) — главное для расширяемости

**Правило размещения.** Порт, который доменный сервис использует напрямую (например,
`MatchingPipeline`/стратегии зовут `MusicPlatformGateway` или `TrackMatchRepository`), обязан сам
лежать в `domain`-пакете — `import-linter` (контракт «Domain purity — no inner layers») запрещает
любому `domain` импортировать `application` любого модуля, в том числе свой собственный. Выбор
конкретного `domain`-пакета зависит от того, кто ещё использует порт:
- нужен нескольким контекстам (сейчас или на следующих этапах) → `shared_kernel/domain` — он
  единственный исключён из правила независимости контекстов, поэтому доступен любому домену;
- нужен только одному контексту → `domain`-пакет этого контекста (например,
  `modules/matching/domain/ports.py` для `TrackMatchRepository`).

Порт, которым пользуется только `application`-слой (его реализацию подставляют через конструктор
use case, а не вызывают из домена напрямую), можно оставлять в `application/ports.py` того же модуля.

Так, с этапа 2: `MusicPlatformGateway` и его DTO `TrackQuery`/`TrackCandidate` лежат в
`shared_kernel/domain`; `TrackMatchRepository`, а также заглушки `AudioRecognizer`/
`FingerprintComparer` (ещё не подключены к пайплайну, задел под этап 6) — в
`modules/matching/domain/ports.py`. С этапа 3 `MusicPlatformGateway` дополнен методами
`get_playlist`/`create_playlist`/`add_tracks`/`get_library`/`add_to_library`/
`library_insert_order` (раздел ниже — актуальные сигнатуры, реализованные в коде).

```python
class MusicPlatformGateway(Protocol):
    platform: Platform
    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]: ...
    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]: ...
    async def get_playlist(self, ref: PlaylistRef) -> PlaylistSnapshot: ...
    async def create_playlist(self, title: str, description: str | None) -> PlaylistRef: ...
    async def add_tracks(self, playlist: PlaylistRef, tracks: Sequence[ExternalTrackRef]) -> AddResult: ...
    # Не async def: это asynchronous generator, а не корутина, возвращающая итератор —
    # вызывающий код сразу делает `async for ... in gateway.get_library()`, без await
    # перед циклом. TrackSnapshot из раздела 7/8 (черновик) — на практике это тот же
    # TrackCandidate, отдельный тип не завели.
    def get_library(self) -> AsyncIterator[TrackCandidate]: ...
    async def add_to_library(self, tracks: Sequence[ExternalTrackRef]) -> AddResult: ...
    def library_insert_order(self) -> InsertOrder: ...                     # TOP | BOTTOM — для сохранения порядка
    # с этапа 4b:
    async def playlist_info(self, ref: PlaylistRef) -> PlaylistInfo: ...   # шапка без треков, owner_external_id
    async def is_own_library(self, ref: PlaylistRef) -> bool: ...          # ссылка на «свою» медиатеку
    def playlist_capacity(self) -> int | None: ...   # 4b-2: SoundCloud — 500; None — без лимита
    # Все методы бросают ошибки shared_kernel/domain/errors.py (см. ниже).

# TrackCandidate с этапа 4b: + artists: tuple[str, ...], cover_url: str | None.
# С 4b-2: + uploader: str | None (кто залил), rights_holder: bool (заливка правообладателя),
# restriction: TrackRestriction | None (PREVIEW_ONLY — без подписки только превью, Go+).
# Версию отдельным полем не заводим: адаптер склеивает её в title ("Starboy (Live)"),
# matching извлекает её оттуда же, откуда и у остальных площадок.

# shared_kernel/domain/errors.py (этап 4b) — контракт шлюза:
# PlatformAuthError (401, токен не принят) | PlatformRateLimitedError(retry_after_seconds)
# | PlatformUnavailableError (сеть/5xx/таймаут — временная) | PlatformRegionError (гео)
# | PlaylistNotFoundError | PlaylistNotWritableError (чужой плейлист, 403 на запись —
# аккаунт НЕ протух); PlatformNotSupportedError, UnsupportedLinkError(reason).

class UrlExpander(Protocol):                      # shared_kernel/domain/ports.py
    def is_short_link(self, host: str) -> bool: ...
    async def expand(self, url: str) -> str: ...   # раскрытие коротких ссылок

# enrichment
class LyricsProvider(Protocol):
    async def find(self, track: TrackIdentity) -> LyricsResult | None: ...   # plain / synced (LRC) / только url
class ArtworkProvider(Protocol):
    async def find(self, track: TrackIdentity) -> Artwork | None: ...        # url, размер, источник
class MetadataProvider(Protocol):
    async def lookup(self, query: TrackQuery) -> list[TrackMetadata]: ...    # Deezer / iTunes: ISRC-мост

# backups
class PlaylistExporter(Protocol):
    format: ExportFormat                                                       # JSON | CSV | XLSX | M3U8 | XSPF | TXT
    def export(self, snapshot: CollectionSnapshot) -> bytes: ...
class PlaylistImporter(Protocol):
    def parse(self, data: bytes) -> list[TrackQuery]: ...
class FileStorage(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def presigned_url(self, key: str, ttl_s: int) -> str: ...

# shared_kernel/application/ports.py (с этапа 4a). Фабрика принимает собранный VO
# AccountAccess, а не ConnectedAccount: shared_kernel не импортирует accounts.domain
# (была бы обратная зависимость). Собирает VO сам accounts — через AccountAccessProvider.
@dataclass(frozen=True)
class PlatformCredentials:           # расшифрованные токены, поля repr=False
    access_token: str; refresh_token: str | None; expires_at: datetime | None
@dataclass(frozen=True)
class AccountAccess:
    account_id: UUID; user_id: UUID; platform: Platform; transport: Transport
    external_user_id: str                # 4b: проверен через профиль площадки
    credentials: PlatformCredentials

class GatewayFactory(Protocol):
    def for_account(self, access: AccountAccess) -> MusicPlatformGateway: ...
    # выбирает транспорт по access.transport: official / unofficial / extension
    def supports(self, platform: Platform) -> bool: ...   # 4b; иначе PlatformNotSupportedError
    # Реализация — integrations/platforms/registry.py: PlatformGatewayFactory(площадка → сборщик);
    # что чем обслуживается (Яндекс — настоящий адаптер, platforms.fake — фейк), решает container.py.

class PlatformRateLimiter(Protocol):     # 4b; RedisTokenBucketLimiter (infrastructure/ratelimit)
    async def acquire(self, platform: Platform, account_id: UUID) -> None: ...
    async def penalize(self, platform: Platform, account_id: UUID, seconds: float) -> None: ...  # 429 → пауза аккаунта
    async def recent_requests(self, platform: Platform, account_id: UUID, minutes: int) -> int: ...  # для логов

class CredentialsRefresher(Protocol):    # 4b-2; accounts.application.AccountCredentialsRefresher
    # renew(current) вызывается под SELECT ... FOR UPDATE строки аккаунта (своя транзакция):
    # параллельный воркер дождётся и получит уже продлённые токены, а не пойдёт продлевать
    # ротированным refresh_token. Новые токены шифруются и сохраняются там же.
    async def refresh(self, account_id: UUID, stale: PlatformCredentials,
                      renew: Callable[[PlatformCredentials], Awaitable[PlatformCredentials]]
                      ) -> PlatformCredentials: ...

class AccountAccessProvider(Protocol):   # реализация — accounts.application.AccountAccessService
    async def get(self, user_id: UUID, account_id: UUID) -> AccountAccess: ...
    async def for_platform(self, user_id: UUID, platform: Platform) -> AccountAccess: ...
    async def update_credentials(self, account_id: UUID, credentials: PlatformCredentials) -> None: ...
    async def report_auth_failure(self, account_id: UUID) -> bool: ...   # 4b, см. 11d
    # все методы бросают AccountNotAvailableError (нет / чужой / отключён / не та площадка)

# accounts/application/ports.py
class TokenCipher(Protocol):         # реализация — infrastructure/security/aes_gcm.py
    def encrypt(self, plaintext: str, *, aad: bytes) -> bytes: ...
    def decrypt(self, ciphertext: bytes, *, aad: bytes) -> str: ...
class PlatformProfileFetcher(Protocol):   # 4b: профиль по токену; Yandex/FakeProfileFetcher
    platform: Platform
    async def fetch(self, credentials: PlatformCredentials) -> PlatformProfile: ...  # external_user_id, display_name
class OAuthProvider(Protocol):       # FakeOAuthProvider; 4b-2: SoundCloudOAuthProvider (по флагу)
    platform: Platform
    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str: ...
    async def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> OAuthGrant: ...

# identity/application/ports.py
class SessionTokenService(Protocol): # реализация — RedisSessionStore
    async def issue(self, user_id: UUID) -> str: ...
    async def resolve(self, token: str) -> UUID | None: ...
    async def revoke(self, token: str) -> None: ...
    async def revoke_all(self, user_id: UUID) -> None: ...

class AudioSource(Protocol):
    async def fetch_fragment(self, ref: ExternalTrackRef, seconds: int) -> AudioFragment: ...

class AudioRecognizer(Protocol):
    async def recognize(self, fragment: AudioFragment) -> RecognitionResult | None: ...

class FingerprintComparer(Protocol):
    async def similarity(self, a: AudioFragment, b: AudioFragment) -> float: ...

# TaskQueue/EventPublisher/UnitOfWork — реализованы с этапа 3 в
# shared_kernel/application/ports.py (нужны нескольким контекстам, вызываются из
# application, не из домена):
class TaskQueue(Protocol):
    async def enqueue(self, task_name: str, *args: Any, **kwargs: Any) -> None: ...
class EventPublisher(Protocol):
    async def publish(self, topic: str, payload: Mapping[str, Any]) -> None: ...
class UnitOfWork(Protocol):
    # __aexit__ — только защитный rollback при исключении, НЕ авто-коммит на чистом
    # выходе из `async with`; commit() вызывается явно use case'ом.
    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
    def track(self, aggregate: AggregateRoot) -> None: ...   # какие агрегаты собрать события с на commit()
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
```

Новая площадка = новый пакет в `integrations/platforms/` + регистрация в DI.
Ни домен, ни use cases не меняются.

---

## 9. Доступ к площадкам

| Площадка | Чтение | Запись | Аудио для распознавания |
|---|---|---|---|
| Spotify | Client Credentials (официально) | расширение / «свой Client ID» / OAuth dev-mode (≤5 польз.) | нет (DRM) — не нужно, есть ISRC |
| Яндекс | `yandex-music` | `yandex-music` → расширение | да |
| VK | токен (`vkpymusic`), пароли не храним | токен → расширение | да |
| SoundCloud | api-v2 по токену из cookie сайта (UNOFFICIAL, по умолчанию); официальный API — если в Settings есть приложение (Artist Pro) | то же | да |
| YouTube Music | `ytmusicapi` | `ytmusicapi` | да (`yt-dlp`) |

**Браузерное расширение** — отдельный транспорт: бэкенд кладёт задачу
(`spotify.add_tracks`, `vk.search`, …), расширение забирает её по WebSocket и выполняет в сессии
пользователя. На сервере не хранятся ни пароли, ни cookie.

---

## 10. База данных (PostgreSQL)

```
users               id, email UNIQUE (нормализован: lower), password_hash (argon2id), created_at
connected_accounts  id, user_id → users ON DELETE CASCADE, platform, transport, external_user_id,
                    display_name, access_token_enc, refresh_token_enc (bytea, AES-GCM),
                    expires_at, status (active|expired|disconnected), connected_at,
                    UNIQUE(user_id, platform, external_user_id),
                    UNIQUE(user_id, platform) WHERE status = 'active'
-- transfers.user_id → users ON DELETE CASCADE; transfers.source_account_id/destination_account_id
-- → connected_accounts ON DELETE RESTRICT (аккаунты не удаляются, только DISCONNECTED)
-- Сессии — не в Postgres, а в Redis: session:<sha256(token)> → user_id, user_sessions:<user_id>
canonical_tracks    id, isrc, title_norm, artist_norm, duration_ms, mbid
platform_tracks     id, platform, external_id, canonical_id, raw_title, raw_artist, duration_ms, isrc,
                    raw jsonb, UNIQUE(platform, external_id)
track_matches       id, source_pt_id, target_platform, target_pt_id, method, score, confirmations,
                    restriction (4b-2: preview_only), UNIQUE(source_pt_id, target_platform)
recognitions        id, platform_track_id, provider, result jsonb, created_at
transfers           id, user_id,
                    source_kind (playlist|library), source_platform, source_playlist_id,
                    destination_kind (existing|new|library), target_platform, target_playlist_id,
                    new_playlist_title, resolved_target_platform,
                    resolved_target_ids jsonb (4b-2: созданные плейлисты «(1/N)…»),
                    cursor jsonb (для возобновления),
                    status, total, pending, matched, uncertain, not_found, added, failed
                    (счётчики — 11c; recognized — этап 6), created_at, updated_at (NOT NULL)
transfer_items      id, transfer_id, position, source_pt_id, match_id, status, candidates jsonb,
                    match_restriction (4b-2), processed_at
artworks            canonical_id, provider, url, width, height, dominant_color, fetched_at
lyrics_refs         canonical_id, provider, page_url, has_synced, cached_until
                    -- сам текст: только кэш в Redis с TTL, не в Postgres
cleanup_reports     id, user_id, kind (duplicates|diff|unavailable), payload jsonb, status, created_at
backups             id, user_id, source (playlist|library), platform, format, storage_key, tracks_count, created_at
backup_schedules    id, user_id, source, platform, formats text[], cron, enabled, last_run_at
```
Файлы бэкапов — в S3-совместимом хранилище (SeaweedFS в dev; MinIO не используем — с конца 2025 его образы не публикуются).
- Расширения: `pg_trgm` (GIN-индексы на `title_norm`, `artist_norm`), `unaccent`.
- `track_matches` — глобальный кэш соответствий; `confirmations` растёт от ручных подтверждений.
- Миграции только через Alembic, автогенерация + ручная проверка.

---

## 11. Фоновая обработка

| Очередь ARQ | Задача | Параллельность |
|---|---|---|
| `transfer` | оркестрация: читаем плейлист, создаём items, раздаём задачи | средняя |
| `match` | поиск + скоринг одного трека | высокая, ограничена rate limiter-ом площадки |
| `recognize` | ffmpeg + распознавание (5–15% треков) | низкая |
| `write` | создание плейлиста, добавление пачками | 1 на аккаунт |
| `ru` | всё, что требует РФ-IP (VK, Яндекс) — отдельный воркер | по площадке |

- Rate limit: token bucket в Redis на `(platform, account)` (реализован на этапе 4b, см. 11d).
- Капча VK → `CaptchaRequired` → фронт показывает картинку → `resume`.
- Аудио-фрагменты не храним: скачали → распознали → удалили. Результаты кэшируем в `recognitions`.

**Реализация с этапа 3:** `transfer`/`match`/`write` — три ARQ-таска
(`modules/transfers/presentation/tasks.py`: `run_transfer`/`run_match`/`run_write`), физически один
воркер-процесс (как сейчас и в `docker-compose.yml` — один сервис `worker`). Разные
concurrency/rate-limit по площадкам, описанные в таблице выше, НЕ обеспечены на этом этапе —
единственный реальный ограничитель сейчас отсутствует (token bucket появится вместе с реальными
адаптерами, этап 4). Разнесение по отдельным процессам/`queue_name` — вопрос конфигурации
(`TaskQueue`-порт это абстрагирует), не переписывания кода, когда дойдёт очередь.

Плюс cron-таск `sweep_stale_transfers` (каждые 5 минут, `WorkerSettings.cron_jobs`) —
`SweepStaleTransfersUseCase` подбирает переносы в `QUEUED`/`RUNNING`, которые не обновлялись
дольше 10 минут, и переставляет их в очередь заново (детали и границы — ниже в 11a).

### 11a. Этап 3 — что сделано и какой долг оставлен

- **`catalog` — опережающий минимальный срез**, хотя у контекста нет своего номера в разделе 14:
  `CanonicalTrack`/`PlatformTrack` (домен), `EnsurePlatformTrackUseCase` (application) — единственная
  публичная точка, через которую `matching` и `transfers` резолвят `ExternalTrackRef → platform_track.id`
  (идемпотентный get-or-create через `INSERT ... ON CONFLICT DO NOTHING` + `SELECT`, без
  find-затем-save — несколько `run_match`-джоб одновременно резолвят один и тот же трек).
  Без presentation/HTTP — только то, что нужно другим контекстам.
- **`transfer_items.match_id` (FK на `track_matches`) не реализован** — вместо него
  `transfer_items` хранит результат матчинга денормализованно (`match_target_platform`,
  `match_target_external_id`, `match_method`, `match_score`). Причина: при ручном разрешении
  (`resolve_item`, `method="manual"`) строки в `track_matches` не существует, а создавать её
  специально ради FK было бы искусственно. Восстановить связь можно на этапе `library_tools`,
  когда появится осмысленный сценарий её использования.
- ~~**`Transfer.user_id`, `LibrarySource.account_id`, `LibraryDestination.account_id` — просто
  `UUID`, без FK.**~~ Закрыто на этапе 4a (миграция `7c1e4a9b2d30`, см. 11b).
- **`LinkResolver`/`UrlExpander` (раздел 7, разбор ссылки плейлиста) не реализованы** —
  осмысленны только с реальными адаптерами площадок (этап 4). `POST /transfers` на этом этапе
  принимает структурированный `source`/`destination` (`platform` + `external_id`/`account_id`),
  не сырую ссылку; тело запроса — дискриминированный union 1:1 с `TrackSource`/`TrackDestination`,
  чтобы этап 4 добавил только ветку "raw URL" без переформатирования контракта.
- **Нет transactional outbox.** `SqlUnitOfWork.commit()`: `session.commit()` → публикация событий
  в Redis. Если `EventPublisher.publish()` упадёт уже после успешного `session.commit()`, событие
  теряется безвозвратно (at-most-once). Для live-прогресса по SSE это осознанно приемлемо — клиент
  при реконнекте получает снэпшот текущего состояния через `GetTransferUseCase` первым сообщением
  (`event: snapshot`) до перехода в live-подписку на канал `transfer:{id}`. **Sweeper (ниже) outbox
  не заменяет**: он восстанавливает застывшую *обработку* переноса (переставляет джобы в очередь),
  но не восстанавливает потерянные *события* — SSE-клиент, который был подключён в момент потери
  события, его не увидит; следующий снэпшот при реконнекте покажет актуальное состояние, но без
  промежуточного шага.
- **Sweeper застывших переносов** (`SweepStaleTransfersUseCase`, cron `sweep_stale_transfers`,
  каждые 5 минут). Покрывает только `QUEUED`/`RUNNING` дольше 10 минут без обновления:
  `QUEUED` → повторный `run_transfer` (idempotent — `ProcessTransferUseCase` увидит статус не
  `QUEUED` и выйдет no-op, если его всё же обработали); `RUNNING` → `run_match` на каждый item
  в статусе `PENDING` (idempotent тем же локом/проверкой статуса, что и обычная повторная
  доставка ARQ). `REVIEW` не трогаем — это легитимное ожидание ручного решения, не "застывание".
  `WRITING` тоже не трогаем — вне объёма этого этапа.
- **`MatchingPipelineFactory`** (`modules/matching/application/ports.py`,
  реализация — `DefaultMatchingPipelineFactory` в `pipeline_factory.py` того же пакета).
  `MatchTransferItemUseCase` просит у фабрики готовый `MatchingPipeline` под конкретную
  `target_platform`, не собирая его сам из `GatewayFactory`/`TrackNormalizer`/`MatchScorer` —
  это внутренняя забота `matching`, не `transfers`. Порт в `matching.application`, а не
  `shared_kernel`: пока единственный потребитель — `transfers`, через публичный application-слой
  `matching` (тот же канал, что и `ResolveTrackMatchUseCase`/`EnsurePlatformTrackUseCase`).
- **ARQ at-least-once и идемпотентность.** Повторная доставка одной и той же джобы не должна
  задвоить работу: `ProcessTransferUseCase`/`ResolveUncertainItemUseCase`/`WriteTransferUseCase`
  читают `Transfer` через `TransferRepository.get_for_update()` (`SELECT ... FOR UPDATE` на строке
  `transfers`) — конкурентная доставка блокируется на этом локе до коммита первой попытки, затем
  видит уже изменённый статус и выходит no-op. `MatchTransferItemUseCase` с этапа 4a работает
  иначе — точечно, см. 11c.
- ~~**Побочный эффект до commit.** `WriteTransferUseCase.create_playlist` … повторная доставка
  `run_write` создаст плейлист повторно.~~ Закрыто после 4b-1.1 (см. 11d): создание плейлиста —
  отдельный шаг с немедленным коммитом `resolved_target`.
- **`StartTransferUseCase`/`ProcessTransferUseCase`/`MatchTransferItemUseCase`/
  `ResolveUncertainItemUseCase`: `TaskQueue.enqueue(...)` вызывается строго после успешного
  `uow.commit()`**, не внутри транзакции — иначе воркер может схватить джобу для записи, которую
  откатившаяся транзакция не создала/не изменила.
- **In-memory фейк-площадка** (`integrations/platforms/fake/`) — единственная реализация
  `MusicPlatformGateway`/`GatewayFactory` до этапа 4. Общий статический демо-каталог (3 трека) с
  одним ISRC на все площадки (разные `external_id`), чтобы перенос между двумя инстансами фейка
  находил совпадения через `IsrcStrategy`. `add_tracks`/`add_to_library` ничего не персистируют —
  только отвечают "успех".
- **Грабли при подписке на Redis pub/sub**: `redis.asyncio.Redis` без `decode_responses=True`
  (как собран `ArqRedis` через `create_pool`) отдаёт `message["data"]` из `pubsub.listen()` как
  `bytes`, не `str`. Если это передать дальше как есть (например, в `sse_starlette` — он
  сериализует через `str(...)`), получится буквальный Python-репр `b'...'` вместо самого JSON.
  `transfer_events` (`modules/transfers/presentation/api.py`) явно делает `.decode("utf-8")` —
  тот же паттерн нужен будет любому будущему подписчику на Redis pub/sub (enrichment/recognition).
- **`backend/Dockerfile` — раздельные таргеты `prod`/`dev`.** `prod` (последний стейдж — то, что
  соберёт `docker build .` без `--target`) без dev-зависимостей и без `tests/`. `dev` — с
  dev-группой (`pytest`, `testcontainers`, ...) и скопированным `tests/`; его использует
  `docker-compose.override.yml` (`build.target: dev`), который compose подхватывает автоматически.
  Докер-сокет (нужен testcontainers внутри `api`/`worker` для `docker compose run --rm api pytest
  tests/integration`) — тоже только в `docker-compose.override.yml`, не в основном
  `docker-compose.yml`: прод-конфигурация не должна знать про тестовую обвязку.

### 11b. Этап 4a (identity + accounts) — что сделано и какой долг оставлен

**Сделано**
- **identity**: `User` (email + argon2id), `/auth/register|login|logout|logout-all|me`.
  Регистрация сразу логинит.
- **Сессии — непрозрачный id в Redis, не JWT.** Токен `secrets.token_urlsafe(32)` лежит в
  httpOnly cookie. В Redis хранится `session:<sha256(token)>` → `user_id` с TTL (сырой токен в
  Redis не пишем) и индекс `user_sessions:<user_id>` для «выйти со всех устройств».
  Logout удаляет ключ на сервере, т.е. сессию можно отозвать.
- **Cookie**: `HttpOnly`, `SameSite=Lax`, `Path=/`, без `Domain`. В проде (`cookie_secure=true`)
  — `Secure` и имя `__Host-sp_session`, в dev по http — `sp_session`.
  **CORS** — только allowlist из `Settings.cors`, `allow_credentials=True`, `"*"` запрещён.
- **Rate limit** `/auth/login` и `/auth/register`: 5 попыток в минуту на (IP, sha256(email)),
  фиксированное окно в Redis (`infrastructure/ratelimit`), при превышении 429 + `Retry-After`.
  Неизвестный email при входе всё равно проходит `verify` по хешу-пустышке, чтобы время ответа
  не выдавало, есть ли такой email.
- **accounts**: `ConnectedAccount` (platform, transport, external_user_id, статус
  active/expired/disconnected). `/accounts` — список, ручное подключение токеном,
  отключение. Отключение стирает токены и оставляет строку: на неё ссылаются FK из `transfers`.
- **Токены зашифрованы AES-256-GCM** (`TokenCipher`, ключ из `Settings.security`). Формат:
  `версия(1) | nonce(12) | ciphertext+tag`. AAD = `connected_account:<id>:<access|refresh>`,
  поэтому шифротекст нельзя переставить в другую строку или поле. Домен видит только `EncryptedToken`.
- **OAuth-каркас**: `/accounts/{platform}/oauth/start` → площадка →
  `/accounts/{platform}/oauth/callback` → редирект на фронт. state одноразовый (Redis `GETDEL`,
  TTL 10 мин) и привязан к пользователю, начавшему поток (защита от login-CSRF); PKCE S256.
  Сейчас есть только `FakeOAuthProvider` для `settings.oauth.fake_platforms`. Настоящие клиенты
  Spotify, SoundCloud и Google подключаются вместе с адаптерами (4b) реализацией `OAuthProvider`.
- **`GatewayFactory.for_account(AccountAccess)`** вместо `for_platform`. transfers получает доступ
  через `AccountAccessProvider` (порт `shared_kernel`) и не импортирует `accounts`. Явный `account_id`
  (медиатека) берётся как есть, иначе берётся единственный активный аккаунт пользователя на
  площадке. `StartTransferUseCase` проверяет аккаунты до постановки в очередь (422). Если аккаунт
  отключили посреди переноса, `run_transfer`/`run_match`/`run_write` переводят перенос в
  `FAILED("account_unavailable")` без исключения из таска.
- **Правило: в ARQ-задачи, события и логи — только `account_id`/`transfer_id`, никогда токены.**
  `AccountAccess` резолвится заново внутри воркера; поля с токенами объявлены `repr=False`.
- **`AccountAccessProvider.update_credentials`** — для адаптеров, обновивших OAuth-токены.
  Повторно шифрует и пишет через `AccountCredentialsWriter` в **отдельной транзакции** (своя сессия
  из `async_sessionmaker`), поэтому откат переноса не теряет новый refresh_token. Вызывающая
  транзакция не должна сама держать лок на этой строке `connected_accounts`.
- `SqlUnitOfWork` переехал в `infrastructure/db/uow.py`: им пользуются три контекста.
- Presentation других контекстов получает пользователя через
  `identity.presentation.dependencies.CurrentUserId`. Это публичная точка identity для HTTP-слоя.
  `user_id` больше не принимается в теле `POST /transfers`. Чужой перенос — 404.

**Долги**
- **Перебор email через 409** на `/auth/register`. Rate limit делает перебор дорогим, но не
  невозможным. Вариант на будущее — подтверждение email с одинаковым ответом.
- **Один активный аккаунт на площадку** (частичный UNIQUE). Нельзя перенести VK→VK между двумя
  своими аккаунтами. Для этого нужен явный `account_id` у `ExistingPlaylist`/`NewPlaylist`/`PlaylistSource`.
- Сессии с фиксированным TTL, без sliding-продления при активности.
- Сам refresh OAuth-токенов (по `expires_at`) — задача адаптеров 4b; порт `update_credentials` готов.
- Нет ротации ключа AES; под неё оставлен байт версии в формате шифротекста.
- ~~`external_user_id` при ручном подключении токеном не проверяется у площадки.~~ Закрыто на
  этапе 4b: id и имя берутся из профиля площадки (см. 11d).
- CSRF закрывается сочетанием SameSite=Lax, JSON-тел (cross-origin JSON требует preflight) и CORS-allowlist.
  Отдельного CSRF-токена нет.
- За обратным прокси нужен uvicorn `--proxy-headers --forwarded-allow-ips` (этап «Прод»), иначе
  rate limit считает IP прокси, общий для всех.
- VK и Яндекс подключаются только ручным вводом токена (позже — через расширение): их
  музыкальный API не даёт публичного OAuth. **Пароли площадок не принимаем никогда.**
- Миграция `7c1e4a9b2d30` удаляет переносы-сироты этапа 3 (`user_id` без пользователя), иначе
  FK не создать. Прод-данных на тот момент нет.

### 11c. Конкурентность run_match и кэш track_matches (исправлено в 4a)

**Баг.** Два переноса одних и тех же треков (другой пользователь, повтор того же плейлиста)
матчат треки параллельно. Оба не находят соответствие в кэше и оба делают INSERT
`track_matches` с новым UUID. Второй падает на `UNIQUE(source_pt_id, target_platform)`, джоба
`run_match` умирает, item остаётся PENDING, перенос навсегда застревает в RUNNING.

**track_matches — upsert, побеждает первая запись.** `SqlTrackMatchRepository.save()` делает
`INSERT ... ON CONFLICT (source_pt_id, target_platform) DO NOTHING` и затем читает строку,
которая реально лежит в кэше. Порт возвращает её, и `ResolveTrackMatchUseCase` подменяет свой
кандидат на закэшированный: все переносы видят одно соответствие. `record_confirmation` —
атомарный `SET confirmations = confirmations + 1`.

**run_match не сохраняет агрегат целиком.** Выбрано **точечное обновление одного item +
атомарные счётчики**, а не optimistic locking:
- при optimistic locking (version + retry) сотни параллельных джоб одного переноса конфликтуют
  почти всегда. Ретрай — это повторный сетевой матчинг, а число конфликтов растёт ~квадратично
  от размера плейлиста;
- старый вариант (`get_for_update` на весь перенос) не терял обновлений, но держал лок на всё
  время сетевого матчинга, то есть превращал параллельный матчинг в последовательный.

Как устроено сейчас:
- `run_match` читает «шапку» переноса (`get_header`, без items) и один item;
- `save_item_outcome` условно обновляет этот item (`WHERE status = 'pending'` — защита от
  повторной доставки: из PENDING item переводит ровно одна транзакция);
- затем атомарно сдвигает счётчики `transfers` (`pending = pending - 1, matched = matched + 1`
  и т.д.) с `RETURNING`;
- джоба, получившая `pending = 0`, делает условный переход `RUNNING → REVIEW|WRITING`
  (`transition_status`) и только при успехе вызывает доменный `Transfer.finish_matching()`
  (событие `TransferWritingStarted`) и ставит `run_write`.

Счётчики (`TransferProgress`: total/pending/matched/uncertain/not_found/added/failed) хранятся
в колонках `transfers`. Полное сохранение агрегата (`run_transfer`, resolve, `run_write` —
моменты, когда параллельных `run_match` нет) пересчитывает их из items. Атомарный UPDATE
счётчиков — последняя запись в транзакции `run_match`, поэтому порядок локов у всех джоб
одинаковый и дедлоков нет. Лок строки `transfers` держится только до коммита.
Падение переноса из `run_match` (аккаунт отключён) — тоже условный переход `RUNNING → FAILED`,
без полного save.

**Ограниченный retry.** ARQ не повторяет джобу на обычном исключении. `run_match` сам ловит
ошибку и до `MATCH_MAX_TRIES = 3` попыток бросает `arq.Retry` с нарастающей задержкой.
После этого `FailTransferItemUseCase` переводит item в `FAILED` (событие
`TrackProcessingFailed`) — точно так же точечно. Перенос продолжает остальные треки и сам
переходит в REVIEW/WRITING, если этот item был последним. Sweeper такой item больше не
трогает: он уже не PENDING.

**Дрейф схемы.** Остальные интеграционные тесты проверяют поведение и не замечают, что ORM и
миграции разошлись: недостающий в ORM индекс или NOT NULL на поведение не влияет. Поэтому
есть `tests/integration/test_schema_drift.py` — аналог `alembic check`: в чистой БД
`upgrade head` → `compare_metadata` пуст, плюс круг `downgrade base` → `upgrade head`.
Список ORM-модулей один — `bootstrap/models.py:load_orm_models()`, им пользуются и `env.py`,
и тест. Найденный дрейф этапа 3 исправлен: GIN trgm-индексы объявлены в `CanonicalTrackOrm`,
а `transfers.created_at/updated_at` стали NOT NULL новой миграцией `9a2f6c1d4e57`
(backfill + SET NOT NULL).

### 11d. Этап 4b-1 (LinkResolver + Яндекс Музыка) — что сделано и какой долг оставлен

**Сделано**
- **LinkResolver** для всех 5 площадок + раскрытие коротких ссылок без SSRF (раздел 7).
  `POST /transfers` принимает `{"kind": "link", "url": …}` и в source, и в destination
  (существующий плейлист или медиатека); `POST /links/resolve` — предпросмотр для фронта
  (площадка, плейлист/медиатека, название и число треков, если аккаунт подключён). Ошибки — 422
  с `detail.code` (`not_a_playlist`, `unknown_host`, `mix_not_supported`, `platform_not_supported`,
  `account_not_connected`, `playlist_not_found`, `playlist_not_writable`), площадка недоступна — 503.
  Площадка без адаптера разбирается, но перенос отвергается (`GatewayFactory.supports`).
- **YandexGateway** (`integrations/platforms/yandex/`) поверх `yandex-music` 3.x. Встроенный
  aiohttp-транспорт библиотеки заменён `HttpxYandexRequest` (подкласс её `Request`): один
  `httpx.AsyncClient` на процесс, token bucket перед **каждым** HTTP-запросом (включая догрузку
  треков), свой маппинг HTTP-кодов (библиотека сливает 401 и 403 в одно), respx в тестах.
  - id трека — `"<track>:<album>"` или `"<track>"` (трек без альбома допустим);
    `YandexTrackId.parse` — единственное место разбора. Дубли — по track_id без альбома.
  - Плейлист — `"<login|uid>:<kind>"` или uuid; созданный нами — `"<uid>:<kind>"`, приватный.
  - Запись в плейлист — diff к ревизии пачками; `wrong-revision` → перечитать и повторить (≤ 3);
    треки, которые уже есть в назначении, не добавляются и считаются добавленными (повтор
    `run_write` после сбоя не задвоит). Трек без альбома вставляется по одному: отказ площадки →
    `AddResult.failed`, а не падение пачки.
  - «Мне нравится» — `library_insert_order = TOP`. **Порядок внутри пачки
    `users_likes_tracks_add` проверен live-тестом** (`test_batch_like_order_is_reported`,
    3 прогона, 2026-10-03, две пачки с немонотонными id — то есть это не сортировка по id):
    пачка ложится целиком, **первый трек пачки — сверху**, следующая пачка — над предыдущей.
    Поэтому `add_to_library` пишет пачками по `batch_size`, а каждую пачку отправляет
    развёрнутой: последний трек списка (`WriteTransferUseCase` уже развернул его под
    `InsertOrder.TOP`) оказывается на самом верху. Если Яндекс поменяет поведение —
    `library_batch_preserves_order=False` включает запасной режим «по одному».
  - ISRC у Яндекса нет: `search_by_isrc` → `[]`, работает FuzzySearchStrategy.
    Недоступные треки (`available=false`) при чтении пропускаются (в лог — счётчик).
- **Подключение по токену проверяет токен** (`PlatformProfileFetcher`): `external_user_id` и
  `display_name` — из профиля площадки (`/account/status`), присланные клиентом не принимаются.
  Не принят → 422 `invalid_token`; для площадки без проверки профиля подключение по токену
  отвергается (`platform_not_supported`).
- **401 ≠ 403.** 401 → `PlatformAuthError` → `AccountAccessProvider.report_auth_failure`: токен
  **один раз перепроверяется** через профиль; EXPIRED и `FAILED("account_expired")` — только если
  профиль тоже ответил 401. Иначе ошибка считается разовой и идёт retry. 403 на запись →
  `PlaylistNotWritableError` → `FAILED("playlist_not_writable")`, аккаунт не трогаем; 403 на чтение
  → `PlaylistNotFoundError`. `StartTransferUseCase` для `ExistingPlaylist` заранее сверяет
  `playlist_info().owner_external_id` с uid аккаунта → 422 `playlist_not_writable`.
- **Ошибки площадки в переносах.** Терминальные (протух токен, плейлист не найден/чужой, гео,
  площадка не поддерживается) → `FAILED(<reason>)` без исключения из таска. Временные
  (`PlatformUnavailableError`, `PlatformRateLimitedError`, неподтверждённый 401) пробрасываются:
  `run_match` — прежний retry; `run_transfer`/`run_write` — новый `with_platform_retries`
  (≤ `PLATFORM_MAX_TRIES = 3`, задержка не меньше `retry_after`), после последней попытки
  `FailTransferUseCase` → `FAILED("platform_unavailable")` — иначе перенос навсегда остался бы
  в QUEUED (sweeper гонял бы его по кругу) или WRITING (sweeper его не трогает).
- **Token bucket** (`infrastructure/ratelimit/redis_token_bucket.py`): Lua-скрипт, время из Redis
  `TIME`, ключ `ratelimit:tb:<platform>:<account_id>` с TTL. С резервированием: токены уходят в
  минус, каждый ждущий получает своё время ожидания (воркеры не просыпаются пачкой); если ждать
  дольше `max_wait` — разрешение не выдаётся и не резервируется, `PlatformRateLimitedError` →
  повтор задачи. Fixed-window лимитер входа (11b) остался как был.
- Тесты: unit (парсеры ссылок на реальных форматах, версии, ошибки переносов, accounts);
  integration без Docker — адаптер Яндекса и раскрытие ссылок на respx/записанных ответах
  (`tests/fixtures/yandex`); с Docker — token bucket на Redis, API. Live — `pytest -m live` на
  токене из `.env` (`YANDEX_LIVE_TOKEN`), без токена пропускаются; по умолчанию не запускаются
  (`addopts = -m 'not live'`). Сверка формы ответов — `tests/tools/record_yandex.py` (пишет
  вычищенные ответы в `tests/fixtures/yandex/recorded/`, она в .gitignore).

**Ручной e2e (2026-10-03) и что по нему исправлено**
- Запуск одной командой: `make e2e LINK="<ссылка на плейлист Яндекса>" [ACCEPT=1]` (корневой
  `Makefile` → `scripts/e2e-yandex.ps1`): поднимает приложение, применяет миграции,
  регистрирует пользователя, подключает Яндекс токеном из `.env`, переносит плейлист в новый
  приватный и печатает сводку matched/uncertain/not_found из БД.
- Перенос 100 треков Яндекс → Яндекс: 59 сопоставлены за ~30 с (5 разом + 3 запроса/с), затем
  Яндекс ответил **429 с `Retry-After: 600`** сразу 41 задаче — перенос «встал» на 10 минут.
  Исправлено:
  - `PlatformRateLimiter.penalize(platform, account, seconds)`: при 429 транспорт ставит на
    паузу **весь аккаунт** (Lua понижает баланс token bucket так, что следующий токен — не
    раньше `Retry-After`; более длинную паузу не сокращает). Остальные задачи не идут в API, а
    сразу уходят в повтор с оставшимся сроком;
  - у `PlatformRateLimitedError` свой бюджет повторов (`RATE_LIMITED_MAX_TRIES = 12`, у сбоев —
    по-прежнему 3): «подождите» от площадки — не повод отправлять трек в FAILED.
    `WorkerSettings.max_tries` поднят до того же значения — иначе ARQ оборвал бы задачу на 5-й;
  - задержка повтора — не меньше `Retry-After` плюс разброс до 20%, чтобы десятки отложенных
    задач не просыпались в одну секунду;
  - в лог повторов пишется текст ошибки (код/имя ошибки площадки, без токенов);
  - лимит по умолчанию снижен до 3 разом + 1,5 запроса/с; `docker-compose.yml` теперь
    пробрасывает `PLATFORMS__*` в api/worker (раньше настройки из `.env` до контейнеров не
    доходили).
- Ссылки `music.yandex.ru/playlists/<uuid>` содержат uuid **без** префикса `lk.`, и API
  принимает его именно так (`GET /playlist/lk.<uuid>` → 404). Примеры в тестах исправлены.

**Этап 4b-1.1 — экономия запросов** (тот же e2e, повторный прогон: 429 на первом же запросе —
квота аккаунта, а не только частота). Ничего в поведении не меняет, кроме числа запросов:
- **одна площадка — без поиска** (`SamePlatformStrategy`, раздел 7): перенос Яндекс → Яндекс на
  100 треков — ~7 запросов вместо ~205;
- **`POST /transfers` по ссылке не читает шапку плейлиста** (`ResolvePlaylistLinkUseCase(...,
  preview=False)`): её всё равно прочитает `run_transfer`; предпросмотр — только `/links/resolve`;
- **кэш поиска** `integrations/platforms/search_cache.py` (`CachedSearchGateway`, Redis через
  `infrastructure/cache/RedisTextCache`): `search`/`search_by_isrc` по ключу
  `search:<platform>:<sha256(title|artist|isrc|limit)>`, TTL `PLATFORMS__SEARCH_CACHE_TTL_SECONDS`
  (сутки; пустой результат — не дольше часа; 0 — выключен). Публичные данные каталога — общий
  кэш на всех. Сбой Redis не ломает поиск. Подключён только к настоящим адаптерам;
- **данные для подбора лимита**: token bucket считает выданные разрешения по минутам
  (`ratelimit:cnt:…`, TTL 2 ч); при 429 в логе «N запросов за 10 мин, M за 60 мин,
  Retry-After S» — без токенов;
- e2e-скрипт подсказывает, если прогресса нет дольше 30 с.

Сознательно не делаем: парсинг сайта (квоту не обходит, SmartCaptcha, против правил), пул
служебных аккаунтов (обход ограничений), пропуск `run_match` в `ProcessTransfer` (запросов не
экономит, задевает счётчики/статусы).

Исследовать без кода, когда квота восстановится: (1) массовый импорт Яндекса из текстового
списка «Артист — Трек» — есть ли API (тысячи поисков → несколько запросов, но без нашего
контроля версий); (2) квота на токен или на IP — от этого зависит, имеет ли смысл анонимный поиск.

**После 4b-1.1: дубль плейлиста, квота на IP, ETA**
- **Дубль плейлиста при повторе `run_write` закрыт.** `WriteTransferUseCase`: для `NewPlaylist`
  без `resolved_target` — `create_playlist` → `save` → **commit** → снова `get_for_update` и
  проверка статуса (параллельная доставка могла успеть дописать перенос, пока лока не было) →
  запись треков. Упадёт запись — повтор переиспользует плейлист. Регрессия на Postgres:
  `tests/integration/transfers/test_write_idempotency.py` (падение после create → повтор →
  один плейлист). Остаётся только окно «площадка создала плейлист, а процесс упал до нашего
  commit» — его закрывает лишь сага/outbox или поиск своего плейлиста по метке; пока не делаем.
- **Счётчик запросов с сервера (IP).** Транспорт Яндекса учитывает **каждый** HTTP-запрос
  (`PlatformRateLimiter.count_request`, ключи `ratelimit:cnt:<platform>:server:<минута>`), в том
  числе проверку профиля без аккаунта. Лог 429: «аккаунт A10/A60, сервер (IP) S10/S60 запросов
  за 10/60 мин, Retry-After R». Специально 429 не провоцируем — ждём естественных.
- **ETA переноса.** `transfer_items.processed_at` (миграция `d4f1a7c2e8b3`) ставит БД в
  `save_item_outcome`, когда item выходит из PENDING. `GET /transfers/{id}` отдаёт
  `progress` {status, total, pending, matched, uncertain, not_found, added, failed,
  eta_seconds}; `eta_seconds` — только для `running`, по скорости последних 20 треков,
  считая время до *сейчас* (во время паузы площадки оценка растёт, а не замирает); `null` —
  меньше двух обработанных треков. SSE шлёт событие `progress` с тем же телом раз в 3 с,
  в том числе когда доменных событий нет (своя короткая request-scope сессия на опрос), после
  `done`/`failed` — перестаёт.

**Долги**
- **Квота Яндекса — на токен или на IP? (открытый вопрос.)** Ответ даст первый естественный 429
  с новой строкой лога: 429 при малом числе запросов аккаунта, но большом с сервера — квота на
  IP (тогда анонимный поиск бесполезен, а при росте пользователей понадобятся несколько
  исходящих IP/воркер `ru`); 429 при большом числе запросов аккаунта — квота на токен. При
  нескольких серверах счётчик «server» в общем Redis надо будет разделить по хосту.
- Фактический лимит Яндекса неизвестен: 3 + 1,5/с — консервативная оценка, подбирается по
  тем же логам 429 (`make logs`).
- Фикстуры `tests/fixtures/yandex/*.json` составлены по формату API вручную; сверены с
  `recorded/` (2026-10-03): набор используемых полей трека/лайков/профиля совпадает. Поиск
  без `page` API отвергает (400 `validate`) — библиотека его передаёт, рекордер исправлен.
- Как Яндекс отвечает при гео-блоке, точно не проверено: 451 → `PlatformRegionError`; если окажется
  другой код/тело — поправить `transport._error_for`. API Яндекса может требовать РФ-IP
  (воркер `ru`, раздел 11) — пока все запросы идут с одного процесса.
- `playlist_info` у Яндекса читает плейлист целиком (отдельного лёгкого эндпоинта шапки нет).
- `get_library` читает «Мне нравится» одним запросом id + догрузка пачками; постраничного
  чтения через `cursor` переноса (11a) по-прежнему нет.
- ~~Остальные площадки только парсятся.~~ SoundCloud — 4b-2 (11e); дальше YT Music → VK → Spotify.
- Rate limit настроен для Яндекса и SoundCloud; у фейка лимита нет.

### 11e. Этап 4b-2 (SoundCloud) — что сделано и какой долг оставлен

**Сделано**
- **SoundCloudGateway** (`integrations/platforms/soundcloud/`). Логика одна, транспорта два:
  `SoundCloudApi` → `V2Api` (`api-v2.soundcloud.com`, UNOFFICIAL, по умолчанию) и
  `OfficialApi` (`api.soundcloud.com`, OFFICIAL). Пути v2 сверены с таблицей эндпоинтов в JS
  сайта (2026-10-03): лайк — `PUT users/:userId/track_likes/:id`, id лайков —
  `GET me/track_likes/ids`, сет — `POST playlists` / `PUT playlists/:id`.
  - `external_id` плейлиста — путь из ссылки (`<user>/sets/<slug>[/s-<secret>]`, через
    `/resolve`), числовой `"<id>[:s-<secret>]"` (так храним созданные нами) или `<user>/likes`
    (чужие лайки, только чтение). `SoundCloudPlaylistId.parse` — единственное место разбора.
  - В ответе сета полными приходят только первые ~5 треков, остальные — заглушки `{id, policy}`.
    Они догружаются `GET /tracks?ids=` пачками по 50, для приватного сета — с
    `playlistId`/`playlistSecretToken`. Порядок — как в сете, удалённые пропускаются.
  - Запись в сет — замена списка целиком (`PUT`), ревизий нет. То, что уже есть, не
    добавляется и считается добавленным. Сверх `playlist_max_tracks` (500) — в
    `AddResult.failed`.
  - Лайки — по одному запросу, свежие сверху (`library_insert_order = TOP`). Уже
    лайкнутые отсеиваются по `me/track_likes/ids`.
  - Маппинг: артист — `publisher_metadata.artist`, иначе заливщик (`uploader`). ISRC — из
    `publisher_metadata.isrc`, невалидный отбрасывается. Длительность — `full_duration`:
    у `policy=SNIP` `duration` — 30-секундное превью. `SNIP` → `restriction=PREVIEW_ONLY`.
    `BLOCK` (гео) в поиске отсеивается. `search_by_isrc` → `[]`.
- **client_id веб-клиента** (`ClientIdProvider`) берётся из JS-бандлов главной страницы
  (`client_id:"<32>"`, на 2026-10-03 — в последнем бандле). Скачивание только с allowlist
  (`soundcloud.com`, `a-v2.sndcdn.com`). Кэш — в процессе и в Redis `soundcloud:client_id`
  (сутки). Обновление: compare-and-refresh (если другой воркер уже положил новый, сайт не
  качаем), `asyncio.Lock`, не чаще раза в минуту. `client_id_override` — ручной запасной
  вариант.
- **401 токена ≠ 401 client_id.** Проверено: v2 отвечает одинаковым пустым 401 и на битый
  client_id, и на битый токен. Транспорт на 401 обновляет client_id и повторяет запрос.
  Затем, если есть refresh_token, продлевает токен и повторяет ещё раз. Только 401 после этого
  становится `PlatformAuthError` → `report_auth_failure` (перепроверка `/me`) → EXPIRED.
  403 с HTML DataDome (антибот) → `PlatformUnavailableError` (временная, client_id не
  трогаем); прочий 403 → по контексту `PlaylistNotFound`/`PlaylistNotWritable`. 429 →
  `Retry-After` или `reset_time` из тела → `penalize` аккаунта. Лог 429 общий для площадок
  (`rate_limit_log.py`) и включает `bucket` (`by-client` — квота общего client_id сайта).
- **Токены.** Cookie сайта `oauth_token` — JWT со сроком (`exp`) и `client_id` в claims.
  Сайт продлевает его httpOnly-cookie `oauth_refresh_token` через
  `POST secure.soundcloud.com/oauth/token`. Мы делаем то же: `AccountTokens` продлевает токен
  заранее (за 60 с до `exp`) и после 401. Сохранение — через новый порт `CredentialsRefresher`
  (раздел 8): row-lock строки аккаунта, без гонок при ротации refresh_token. Без
  refresh_token токен работает до `exp`. Подключение — `POST /accounts` с `access_token` и
  необязательным `refresh_token`, профиль проверяется через `/me`. Инструкция —
  `docs/SOUNDCLOUD_TOKEN.md`.
- **OFFICIAL** — `SoundCloudOAuthProvider` (authorize `secure.soundcloud.com`, PKCE S256 из
  каркаса 4a) и `OfficialApi`. Регистрируется, только если задан
  `PLATFORMS__SOUNDCLOUD__OFFICIAL__CLIENT_ID/SECRET`. Пустые значения из compose официальный
  API не включают. Live не проверен: нет приложения с Artist Pro.
- **Несколько плейлистов для NewPlaylist.** Если треков больше `playlist_capacity()`,
  `WriteTransferUseCase` создаёт «<название> (1/N)», «(2/N)», ... Каждый создаётся отдельным
  шагом с немедленным коммитом в `Transfer.resolved_targets`, после коммита лок берётся
  заново. Повтор `run_write` переиспользует созданные. Дубли целевых треков место не
  занимают. Регрессия на Postgres — `test_write_idempotency.py` (падение между частями).
  `ExistingPlaylist` не делится: остаток уходит в `failed`.
- **Пометка «только превью».** `restriction` выбранного кандидата проходит через
  `TrackMatch` (колонка `track_matches.restriction`, поэтому не теряется при попадании в кэш)
  → `MatchResult` → `transfer_items.match_restriction` → API (`match.restriction`,
  `candidates[].restriction`). Ручной выбор берёт пометку из кандидатов item.
- Миграция `e7b2c9d4a1f6`: `transfers.resolved_target_ids` вместо
  `resolved_target_playlist_id` (backfill), `track_matches.restriction`,
  `transfer_items.match_restriction`.
- Тесты: respx на фикстурах формы api-v2 (`tests/fixtures/soundcloud`, данные вымышленные).
  Форма сверена с публичными ответами search/resolve/tracks. Рекордер —
  `tests/tools/record_soundcloud.py`, только чтение и вычистка; дополнительно выгружает
  `titles.csv` для разбора нормализатором. Live — `tests/live/soundcloud` с откатом в
  `finally`. Нормализатор проверен на 545 реальных публичных названиях из поиска; в корпус
  `dirty_titles` добавлено 14 пар, итог 114/114 без ложных AUTO.

**Live-проверка 2026-10-03 (токен владельца, домашний IP)**
- Чтение работает: профиль `/me`, лайки, поиск. Токен из cookie оказался старого формата
  `2-…` — не JWT, без срока и без `oauth_refresh_token`. У этой сессии refresh не нужен.
- **Запись через api-v2 закрыта антиботом DataDome**: `POST /playlists`, `PUT track_likes`
  → 403 `x-datadome: protected` и JSON со ссылкой на капчу. То же у анонимного запроса;
  заголовки `Origin`/`Referer` не помогают. Сайт проходит проверку только благодаря JS
  DataDome в браузере (cookie `datadome` и заголовок `X-Datadome-ClientId`). Обходить
  защиту (подставлять cookie браузера, имитировать JS) **не будем**. Поэтому для
  UNOFFICIAL SoundCloud — только источник; запись — через OFFICIAL (OAuth) или расширение
  (этап 10). Решение владельца: расширение переносится раньше — этап 4c (раздел 14).
- С валидным токеном v2 принимает и мусорный client_id. client_id обязателен только для
  запросов без токена (поиск, профиль при подключении). Логика обновления по 401 от этого
  не меняется.

**Долги**
- Запись в SoundCloud через UNOFFICIAL невозможна (DataDome, выше). Сейчас она
  заканчивается `PlatformUnavailableError("антибот")` → повторы → `FAILED("platform_unavailable")`;
  нужен явный отказ.
- Ревизий у сетов нет: если пользователь правит сет параллельно с переносом, одна из правок
  может потеряться (замена списка целиком).
- Квота `by-client` считается на client_id сайта, то есть на весь сервер, а не на аккаунт.
  `penalize` пока ставит на паузу только аккаунт. Если такие 429 появятся, нужен
  серверный штраф.
- DataDome вероятен с IP датацентров. В проде может понадобиться другой исходящий IP или
  расширение (этап 10).
- Хрупкость client_id: если сайт перестанет класть его в бандл, сработает
  `PlatformUnavailableError("client_id не найден")`, а запасной вариант —
  `client_id_override`.
- Метод и тело `PUT playlists/:id`, `POST playlists` и порядок лайков live не подтверждены
  (запись упирается в DataDome). Подтвердятся вместе с OFFICIAL-транспортом или
  расширением.
- Срок жизни веб-JWT неизвестен (live-тест печатает `exp`). Если он короткий, без
  refresh_token долгий перенос упрётся в EXPIRED.
- Продление refresh_token на сервере может разлогинить браузерный профиль, из которого
  токены взяли (ротация). Это описано в инструкции.
- Чужие лайки (`<user>/likes`) — только источник; `playlist_info` для них отдаёт
  `likes_count` пользователя.

### 11f. Этап 4b-3 (квота площадки) — что сделано, план и долги

Повод — e2e «лайки SoundCloud → Яндекс» (2026-10-03): после 11 из 87 треков Яндекс ответил
429 с `Retry-After: 600`, всего через 5–6 запросов за час. Квота важнее частоты.

**Сделано**
- **Запрос из разобранного источника и feat-артисты** (раздел 7). Разбор e2e без новых
  запросов к Яндексу: 4 из 5 not_found давали пустую выдачу из-за ника заливщика в запросе;
  3 из 4 UNCERTAIN — точные совпадения, потерявшие feat-артиста. Теперь они AUTO.
  Второй поиск «той же версии» убран — минус запрос на каждый трек с версией.
- **PAUSED_QUOTA вместо роста бюджета повторов.**
  - `PlatformRateLimitedError` с Retry-After ≥ 60 с (`QUOTA_PAUSE_MIN_SECONDS`), или короткие
    429, исчерпавшие свой бюджет, больше не повторяют задачу и не ведут к FAILED.
    `PauseTransferForQuotaUseCase` ставит на паузу **весь перенос**: условный UPDATE,
    `paused_from` = прежняя фаза (QUEUED/RUNNING/WRITING), `resume_at` = max(старый, новый).
    Ранний 429 чужую более длинную паузу не сокращает. Событие `TransferPausedForQuota`.
  - Трек, на котором случился 429, остаётся PENDING. Новые `run_match` на паузе ничего не
    делают и в площадку не ходят. Результаты задач, начатых до паузы, дописываются
    (`record_item_outcome` допускает PAUSED_QUOTA).
  - Отложенная задача `resume_transfer` ставится на `resume_at` с ключом
    `resume_transfer:<id>:<срок>` — порт `TaskQueue.enqueue_at`, в ARQ это `_defer_until` +
    `_job_id`. `ResumeTransferUseCase` (условный UPDATE, только при `resume_at <= now`)
    возвращает фазу и ставит задачи заново:
    - RUNNING → `run_match` только по PENDING;
    - QUEUED → `run_transfer`;
    - WRITING → `run_write`;
    - если за паузу дописались последние треки — сразу REVIEW/WRITING.
    Событие `TransferResumed`.
  - Sweeper подбирает паузы, просроченные больше чем на 5 минут (задача потерялась).
  - `GET /transfers/{id}` и SSE `progress`: `status: "paused_quota"`, `resume_at`, ETA = null.
    e2e-скрипт печатает «квота площадки, продолжим в HH:MM».
  - Миграция `f3a8b1c6d2e9`: `transfers.resume_at`, `paused_from`, статус в CHECK.
  - Тесты: домен; use cases на фейках; SQL-переходы и сквозной сценарий на Postgres
    (429 посреди матчинга → пауза → resume → `failed = 0`) — реальную площадку не трогают.

**Квота на токен или на IP — замер (ждёт второго токена).** Реальный Яндекс в e2e до замера
не используем. План: маленький зонд `tests/tools/yandex_quota_probe.py` — не больше 30
поисков подряд, остановка на первом 429, печатает только счётчики и `Retry-After`.
1. Токен A (уже на паузе по квоте) — 1 поиск, ожидаем 429.
2. Сразу с того же IP токен B (другой аккаунт).
   - B работает → квота **на токен**: масштаб — пользователями, анонимный поиск бесполезен.
   - B тоже 429 → квота **на IP**: при росте нужны воркер `ru` и несколько исходящих IP
     (этап «Прод»), а экономия запросов становится главным рычагом.
3. Результат и цифры — сюда, в 11d («открытый вопрос») и в план 4c.

**Сокращение поисков — исследование (без запросов к API).**
- *Группировка по артисту.* В `yandex-music` есть `artists_tracks(artist_id, page,
  page_size=20)` (`GET /artists/{id}/tracks`) и поиск `type="artist"`: на артиста 1 поиск +
  ≥ 1 страница. На 87 лайках владельца ~79 различных основных артистов, артистов с ≥ 2
  треками — 4, у них 12 треков → 8–12 запросов вместо 12–24, экономия по медиатеке 0–5 %.
  Окупается только при многих треках на артиста. План: включать при ≥ 3 треках на
  артиста; локальный матчинг тем же скорером; треки не нашедшиеся у артиста (feat у
  чужих артистов, сборники) — обычным поиском. Риски: у крупных артистов сотни треков
  (много страниц), запрос к артисту тоже из квоты.
- *Массовый импорт списком.* В `yandex-music` 3.0.0 метода нет. Эндпоинт веб-версии
  (`music.yandex.ru`, импорт «Артист — Трек») нужно снять из DevTools у владельца — имена и
  формы запросов, без cookie. Если он есть — несколько запросов на весь список вместо
  одного на трек: единственный путь к ускорению в разы. Результат Яндекса прогоняем через
  наш скорер, сомнительное — на ручное подтверждение.
- Порядок: замер квоты → импорт (если эндпоинт найдётся) → группировка опцией.

**Долги**
- Порог 60 с — эвристика: короткие 429 по-прежнему повторяются задачей.
- Пауза — на перенос, а не на аккаунт: второй перенос того же аккаунта узнает о квоте только
  своим 429 (token bucket уже штрафует аккаунт — запросов он не сделает, но паузу получит
  только после своего `max_wait`).
- Окно «задача resume потерялась» закрывает sweeper с задержкой до ~10 минут.

---

## 12. Фронтенд и расширение

- **Feature-Sliced Design** — то же разделение ответственности, что на бэке.
- Экраны: подключение площадок → выбор плейлиста → живой прогресс (SSE) →
  **ревью сомнительных** (кандидаты, обложки, длительность, превью) → история.
- Расширение: привязка к аккаунту одноразовым кодом, WebSocket к бэкенду,
  модули под каждую площадку, host_permissions только на нужные домены.

### Подключение площадок: варианты для фронта

| Площадка | Вариант | Пароль к нам попадает? |
|---|---|---|
| Яндекс | **device flow** (ниже) — основной для фронта; ручной ввод токена (`docs/YANDEX_TOKEN.md`) — запасной | нет |
| SoundCloud | OAuth (если есть приложение); иначе ручной ввод `oauth_token` (+ `oauth_refresh_token`) из cookie (`docs/SOUNDCLOUD_TOKEN.md`) | нет |
| VK, Spotify, YT Music | на своих этапах | нет — пароли площадок не принимаем никогда |

**Яндекс Музыка через device flow (исследование 2026-10-03, кода нет).**
- Яндекс OAuth официально поддерживает вход по коду на странице авторизации
  (yandex.ru/dev/id/doc/ru/codes/screen-code-oauth):
  `POST https://oauth.yandex.ru/device/code` (`client_id`, `device_id`, `device_name`) →
  `device_code`, `user_code`, `verification_url` (`https://oauth.yandex.ru/device`, оно же
  `ya.ru/device`), `interval`, `expires_in` (порядка 300 с). Дальше опрос
  `POST https://oauth.yandex.ru/token` (`grant_type=device_code`, `code=<device_code>`,
  `client_id`, `client_secret`) → `access_token`, **`refresh_token`**, `expires_in`. Ошибки:
  `authorization_pending` (ещё не подтвердил), `slow_down`, `invalid_grant`/`expired_token`.
- **Установленная `yandex-music` 3.0.0 это уже умеет**: `ClientAsync.request_device_code()`
  и `poll_device_token()` (`yandex_music/_client_async/device_auth.py`) с публичными
  client_id/secret Android-приложения Яндекс Музыки. Это тот же client_id, что в
  `docs/YANDEX_TOKEN.md`. Выдаётся обычный токен Музыки.
- Пароль к нам не попадает: пользователь входит на домене Яндекса и вводит там короткий код.
  Сервер видит только выданные токены и хранит их зашифрованными, как сейчас.
- Поток для фронта (реализация — этап 5):
  1. `POST /accounts/yandex/device` → бэкенд вызывает `request_device_code`, кладёт
     `device_code` в Redis (TTL = `expires_in`, ключ привязан к `user_id`, как OAuth state в
     4a) и отдаёт фронту `{flow_id, user_code, verification_url, expires_in, interval}`.
  2. Фронт показывает код и кнопку «Открыть страницу Яндекса» (новая вкладка
     `verification_url`) и раз в `interval` секунд опрашивает
     `GET /accounts/yandex/device/{flow_id}`.
  3. Бэкенд на каждый опрос делает один `poll_device_token` (не чаще `interval`; при
     `slow_down` интервал растёт). `pending` → 202. Токен получен → существующий
     `ConnectAccountUseCase` (проверка профиля `/account/status`, шифрование) → 201 с
     аккаунтом, `device_code` удаляется. `expired`/`denied` → 410/403, фронт предлагает
     начать заново.
  4. Бонус: `refresh_token` и `expires_at` сохраняются, и адаптер может продлевать токен через
     `CredentialsRefresher` (порт из 4b-2) без участия пользователя.
- Оговорки и риски:
  - client_id/secret чужие (приложение Яндекс Музыки), не наши. Это тот же серый статус, что у
    всей интеграции через `yandex-music`. Яндекс может отключить device flow для этого
    клиента, тогда остаётся ручной ввод токена.
  - На странице подтверждения пользователь видит «Яндекс Музыка» и `device_name`. В UI нужно
    прямо объяснить, что он выдаёт доступ к своей Музыке нашему сервису, и передавать
    `device_name` вроде «SyncPlaylists».
  - Фишинг device flow (злоумышленник подсовывает свой код): код показываем только
    залогиненному пользователю в нашем UI, коды извне не принимаем, flow привязан к
    `user_id`.
  - Опрос идёт с сервера и считается в server-счётчике запросов (квота Яндекса может быть на
    IP — 11d).
- Перед реализацией — ручная live-проверка: `request_device_code` → подтвердить код в
  браузере → `poll_device_token` → токен проходит `/account/status`. До неё вывод «работает»
  основан на документации Яндекса и коде библиотеки; live не проверено.

---

## 12a. Docker

| Сервис | Образ | Профиль |
|---|---|---|
| `postgres` | `postgres:17-bookworm` (+ pg_trgm, unaccent; позже pgvector-образ) | default |
| `redis` | `redis:7` | default |
| `s3` | `chrislusf/seaweedfs` (`server -s3`, S3-совместимое хранилище) | default |
| `api` | `backend/Dockerfile` → uvicorn | `app` |
| `worker` | тот же образ → `arq ...WorkerSettings` (очереди transfer/match/write) | `app` |
| `worker-recognize` | тот же образ, очередь `recognize`; ffmpeg + chromaprint внутри | `app` |
| `frontend` | `frontend/Dockerfile` → nginx | `app` |
| `caddy` | `caddy:2` (только прод) | `prod` |

- `postgres:17-bookworm`, а не плавающий `postgres:17`: на момент написания (окт. 2026) тег `17`
  перешёл на Debian trixie и временно не собран под `linux/amd64` (в manifest list только
  arm/386/ppc64le/riscv64/s390x + attestation-записи) — `docker pull`/`run` падает с
  `exec format error`. Как появится amd64-сборка под `17`, можно вернуться на плавающий тег.
- Один backend-образ на три роли; роль задаётся командой запуска.
- Healthchecks у всех сервисов; `depends_on: condition: service_healthy`.
- dev: `docker-compose.override.yml` (создан на этапе 3) переключает `api`/`worker` на таргет
  `dev` образа (dev-зависимости + `tests/`, см. 11a) и даёт `api` доступ к докер-сокету для
  `docker compose run --rm api pytest tests/integration` (testcontainers). Монтирование исходников
  и `--reload` в нём пока не реализованы — остаётся долгом.
- Прод: тот же compose + профиль `prod`; РФ-воркер запускается тем же образом на отдельном сервере.

## 13. Тестирование

- **unit**: домен и use cases на in-memory фейках портов — быстрые, без Docker.
- **integration**: репозитории на Postgres в testcontainers; адаптеры площадок — через записанные ответы (respx).
- **matching-корпус**: `fixtures/dirty_titles.json` — реальные пары «грязное название → правильный трек»;
  метрика точности не должна падать между коммитами.
- `import-linter` контракты: слои и независимость модулей.

---

## 14. Этапы

1. **Скелет**: uv-проект, settings, dishka, FastAPI app factory, Postgres + Alembic, docker-compose, CI-линтеры.
2. **shared_kernel + matching**: VO, нормализатор, скорер, пайплайн, корпус тестов.
3. **transfers** (сделано): агрегат, use cases, репозитории, ARQ, SSE; попутно —
   минимальный срез `catalog` и персистентность `matching` (детали и долги — раздел 11a).
4. **Адаптеры**:
   - 4a. **identity + accounts** (сделано): пользователи, сессии, подключённые аккаунты,
     шифрование токенов, OAuth-каркас, `GatewayFactory.for_account` (детали и долги — 11b);
   - 4b. адаптеры площадок: Яндекс → SoundCloud → YT Music → VK → Spotify (чтение) +
     настоящие OAuth-клиенты. **4b-1 (сделано):** LinkResolver всех площадок, YandexGateway,
     token bucket, проверка токена через профиль, поиск той же версии (детали и долги — 11d).
     **4b-2 (сделано):** SoundCloud — api-v2 по токену + официальный API по флагу,
     перезаливы в матчинге, несколько сетов сверх 500, пометка «только превью»,
     продление токенов (детали и долги — 11e); device flow Яндекса — исследование (раздел 12).
     Live: запись api-v2 закрыта DataDome → SoundCloud через токен — только источник.
   - **4c. Расширение (перенесено с этапа 10 по итогам 4b-2):** WXT, привязка к аккаунту
     одноразовым кодом, WebSocket, транспорт `Transport.EXTENSION` в `GatewayFactory`;
     первая площадка — **запись в SoundCloud** (создание сета, добавление треков, лайки) в
     сессии пользователя, где антибот проходит сам браузер. Дальше этим же каналом —
     Spotify и fallback VK/Яндекса (бывший этап 10). Чтение SoundCloud остаётся на v2.
     После 4c — оставшиеся адаптеры 4b (YT Music → VK → Spotify).
5. **Фронтенд**: подключение, перенос, ревью.
6. **recognition**: ffmpeg, shazamio, ACRCloud, chromaprint.
7. **enrichment**: обложки (Deezer → iTunes → CAA → Genius), ISRC-мост, тексты (Genius API + LRCLIB).
8. **backups**: экспорт во все форматы, импорт из файла, расписания, S3 (SeaweedFS).
9. **library_tools**: дубли, слияние, сравнение площадок, недоступные треки.
10. ~~**Расширение**~~ — перенесено в 4c; здесь остаётся развитие расширения под новые площадки.
11. **Прод**: Caddy, РФ-воркер, Sentry, бэкапы Postgres.
12. **ML-усиление матчинга** (раздел 15) — после того, как накопятся данные ручных подтверждений.

---

## 15. ML в сопоставлении (бесплатно, self-hosted)

Принцип: **отпечатки (Shazam/chromaprint) остаются основой** для узнавания записи по звуку — нейросети
там не лучше. Нейросети добавляются точечно, где правила слабы. Каждая — за портом, включается флагом в settings.

| Порт | Реализация | Зачем | Стоимость |
|---|---|---|---|
| `TextEmbedder` | `multilingual-e5-small` / LaBSE (sentence-transformers, CPU) + **pgvector** | похожесть «Кино — Группа крови» ↔ «Kino - Gruppa Krovi», переводные названия, опечатки | бесплатно, ~мс на трек |
| `TitleParser` | правила → при неудаче локальная LLM (Qwen 2.5 3B/7B через Ollama / llama.cpp) | разбор грязных названий в `{artist, title, version: remix/live/cover/sped up}` | бесплатно, ~1–3 с на CPU, только для «хвоста» |
| `MatchRanker` | LightGBM на признаках (title/artist sim, Δduration, embedding sim, method) | обучение на ручных подтверждениях → точнее пороги, меньше ручной работы | бесплатно, CPU |
| `AudioEmbedder` *(позже)* | CLAP / MERT | отличить оригинал от ремикса/live/кавера, когда chromaprint «не уверен» | бесплатно, тяжелее по CPU |

---

## 16. Идеи на будущее (не утверждены)

Утверждены и перенесены в функциональность: **чистка медиатеки**, **бэкап/импорт**.

Отложены:
- **Telegram-бот** как второй интерфейс (отдельный `presentation`-модуль на aiogram).
- **Автосинхронизация**: подписка «плейлист A → плейлист B», ARQ cron переносит только новые треки.
- **Универсальные ссылки** на трек/плейлист (как song.link).

