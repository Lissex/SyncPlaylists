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
  (MinIO локально), отдаются по временной ссылке.
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
| Файлы | **MinIO** / S3 (aioboto3), экспорт: `openpyxl` (XLSX), stdlib (CSV/JSON/M3U8/XSPF) |
| Площадки | `spotipy`/свой клиент, `yandex-music`, `vkpymusic`, `ytmusicapi`, `soundcloud-v2` / официальный API |
| Качество | **uv**, **ruff**, **mypy --strict**, **pytest**, pytest-asyncio, **respx**, **testcontainers**, **import-linter**, pre-commit |
| Наблюдаемость | **structlog**, **Sentry** |
| Фронтенд | **React 19 + Vite + TS**, TanStack Query/Router, Tailwind + shadcn/ui, **Feature-Sliced Design** |
| Расширение | **WXT + TypeScript**, Manifest V3 (Chrome + Firefox) |
| Инфра | **Docker Compose** (+ MinIO), Caddy (HTTPS); прод: VPS в ЕС + воркер в РФ через WireGuard/Tailscale |

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

class SecuritySettings(BaseModel):
    token_encryption_key: SecretStr      # AES-GCM ключ (base64)
    session_secret: SecretStr

class SpotifySettings(BaseModel):
    client_id: str
    client_secret: SecretStr
    redirect_uri: str

class SoundCloudSettings(BaseModel):
    client_id: str
    client_secret: SecretStr
    redirect_uri: str

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
    spotify: SpotifySettings
    soundcloud: SoundCloudSettings
    recognition: RecognitionSettings = RecognitionSettings()
```

- Settings создаются **один раз** в `bootstrap` и раздаются через DI.
- Домен и application **не импортируют** settings; если use case нужен параметр
  (например, порог скоринга) — он передаётся в конструктор как обычное значение.
- Секреты — только `SecretStr`, в логах не светятся. В репозитории — только `.env.example`.

---

## 7. Доменная модель

### Value objects (`shared_kernel`)
- `Platform` — `SPOTIFY | YANDEX | VK | SOUNDCLOUD | YTMUSIC`
- `ISRC` — валидируется (`^[A-Z]{2}[A-Z0-9]{3}\d{7}$`)
- `Duration` — миллисекунды, `is_close_to(other, tolerance_ms=3000)`
- `ExternalTrackRef(platform, external_id)`, `PlaylistRef(platform, external_id)`
- `MatchScore` — float 0..1 с порогами `AUTO ≥ 0.90`, `UNCERTAIN ≥ 0.70`

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

### Агрегат `Transfer`
```
Transfer (root)
 ├─ id, user_id, source: TrackSource, destination: TrackDestination, resolved_target: PlaylistRef | None
 ├─ status: QUEUED → RUNNING → (PAUSED_CAPTCHA ↔ RUNNING) → REVIEW → WRITING → DONE | FAILED
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
CacheStrategy → IsrcStrategy → FuzzySearchStrategy → AudioRecognitionStrategy → FingerprintVerification
      ↓ нет               ↓ нет             ↓ <0.70                    ↓ нет                    ↓
                                                                                    UNCERTAIN / NOT_FOUND → ручной выбор
```

---

## 8. Порты (интерфейсы) — главное для расширяемости

```python
class MusicPlatformGateway(Protocol):
    platform: Platform
    async def get_playlist(self, ref: PlaylistRef) -> PlaylistSnapshot: ...
    async def search(self, query: TrackQuery, limit: int = 10) -> list[TrackCandidate]: ...
    async def search_by_isrc(self, isrc: ISRC) -> list[TrackCandidate]: ...
    async def create_playlist(self, title: str, description: str | None) -> PlaylistRef: ...
    async def add_tracks(self, playlist: PlaylistRef, tracks: Sequence[ExternalTrackRef]) -> AddResult: ...
    # медиатека («Любимые», «Мне нравится», «Моя музыка», Likes)
    async def get_library(self) -> AsyncIterator[TrackSnapshot]: ...      # постранично, от новых к старым
    async def add_to_library(self, tracks: Sequence[ExternalTrackRef]) -> AddResult: ...
    def library_insert_order(self) -> InsertOrder: ...                     # TOP | BOTTOM — для сохранения порядка

class UrlExpander(Protocol):
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

class GatewayFactory(Protocol):
    def for_account(self, account: ConnectedAccount) -> MusicPlatformGateway: ...
    # выбирает транспорт: official / unofficial / extension

class AudioSource(Protocol):
    async def fetch_fragment(self, ref: ExternalTrackRef, seconds: int) -> AudioFragment: ...

class AudioRecognizer(Protocol):
    async def recognize(self, fragment: AudioFragment) -> RecognitionResult | None: ...

class FingerprintComparer(Protocol):
    async def similarity(self, a: AudioFragment, b: AudioFragment) -> float: ...

class TokenCipher(Protocol): ...
class TaskQueue(Protocol): ...
class EventPublisher(Protocol): ...
class UnitOfWork(Protocol): ...   # async context manager, commit/rollback, сбор событий
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
| SoundCloud | официальный API (нужен Artist Pro у разработчика) | официальный API → v2 | да |
| YouTube Music | `ytmusicapi` | `ytmusicapi` | да (`yt-dlp`) |

**Браузерное расширение** — отдельный транспорт: бэкенд кладёт задачу
(`spotify.add_tracks`, `vk.search`, …), расширение забирает её по WebSocket и выполняет в сессии
пользователя. На сервере не хранятся ни пароли, ни cookie.

---

## 10. База данных (PostgreSQL)

```
users               id, email, created_at
connected_accounts  id, user_id, platform, external_user_id, access_token_enc, refresh_token_enc,
                    expires_at, transport, status
canonical_tracks    id, isrc, title_norm, artist_norm, duration_ms, mbid
platform_tracks     id, platform, external_id, canonical_id, raw_title, raw_artist, duration_ms, isrc,
                    raw jsonb, UNIQUE(platform, external_id)
track_matches       id, source_pt_id, target_platform, target_pt_id, method, score, confirmations,
                    UNIQUE(source_pt_id, target_platform)
recognitions        id, platform_track_id, provider, result jsonb, created_at
transfers           id, user_id,
                    source_kind (playlist|library), source_platform, source_playlist_id,
                    destination_kind (existing|new|library), target_platform, target_playlist_id,
                    new_playlist_title, cursor jsonb (для возобновления),
                    status, total, matched, recognized, uncertain, not_found, created_at, updated_at
transfer_items      id, transfer_id, position, source_pt_id, match_id, status, candidates jsonb
artworks            canonical_id, provider, url, width, height, dominant_color, fetched_at
lyrics_refs         canonical_id, provider, page_url, has_synced, cached_until
                    -- сам текст: только кэш в Redis с TTL, не в Postgres
cleanup_reports     id, user_id, kind (duplicates|diff|unavailable), payload jsonb, status, created_at
backups             id, user_id, source (playlist|library), platform, format, storage_key, tracks_count, created_at
backup_schedules    id, user_id, source, platform, formats text[], cron, enabled, last_run_at
```
Файлы бэкапов — в S3-совместимом хранилище (MinIO в dev).
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

- Rate limit: token bucket в Redis на `(platform, account)`.
- Капча VK → `CaptchaRequired` → фронт показывает картинку → `resume`.
- Аудио-фрагменты не храним: скачали → распознали → удалили. Результаты кэшируем в `recognitions`.

---

## 12. Фронтенд и расширение

- **Feature-Sliced Design** — то же разделение ответственности, что на бэке.
- Экраны: подключение площадок → выбор плейлиста → живой прогресс (SSE) →
  **ревью сомнительных** (кандидаты, обложки, длительность, превью) → история.
- Расширение: привязка к аккаунту одноразовым кодом, WebSocket к бэкенду,
  модули под каждую площадку, host_permissions только на нужные домены.

---

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
3. **transfers**: агрегат, use cases, репозитории, ARQ, SSE.
4. **Адаптеры**: Яндекс → SoundCloud → YT Music → VK → Spotify (чтение).
5. **Фронтенд**: подключение, перенос, ревью.
6. **recognition**: ffmpeg, shazamio, ACRCloud, chromaprint.
7. **enrichment**: обложки (Deezer → iTunes → CAA → Genius), ISRC-мост, тексты (Genius API + LRCLIB).
8. **backups**: экспорт во все форматы, импорт из файла, расписания, MinIO/S3.
9. **library_tools**: дубли, слияние, сравнение площадок, недоступные треки.
10. **Расширение**: запись в Spotify, fallback для VK/Яндекса.
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

