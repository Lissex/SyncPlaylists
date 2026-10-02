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

`TrackNormalizer` дополнительно извлекает `VersionTag` трека (`original | live | remix(remixer) |
acoustic | sped_up | slowed | cover | instrumental | karaoke | extended | radio_edit`) из названия;
`MatchScorer` ограничивает результат потолком `UNCERTAIN`, если версии источника и кандидата не
совпадают (разный тег или разные ремиксеры) — даже при идеальном совпадении текста и длительности.

**Задел на этап адаптеров площадок:** при несовпадении версий `FuzzySearchStrategy` сейчас просто
выставляет `UNCERTAIN` на лучшего найденного кандидата. Когда появятся реальные адаптеры (этап 4),
пайплайн должен сначала **отдельно поискать на целевой площадке ту же версию** (ту же live-запись,
того же ремиксера) — и только если её там нет, предлагать оригинал как `UNCERTAIN` для ручного
подтверждения, а не молча брать первый попавшийся вариант с другой версией.

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
    #
    # ДОЛГ С ЭТАПА 3: пока нет модуля accounts (ConnectedAccount), реализованный порт —
    # for_platform(platform: Platform) -> MusicPlatformGateway, упрощённый (без
    # account/credentials). Живёт в shared_kernel/application/ports.py вместе с
    # UnitOfWork/TaskQueue/EventPublisher — application-порт (вызывается из use case,
    # не из домена), нужен нескольким контекстам. Когда появится accounts (этап 4):
    # сигнатуру НЕЛЬЗЯ чинить импортом modules.accounts.domain.ConnectedAccount в
    # shared_kernel — это обратная зависимость. Либо порт переезжает в accounts, либо
    # принимает уже собранный вызывающей стороной VO (platform + credentials).

class AudioSource(Protocol):
    async def fetch_fragment(self, ref: ExternalTrackRef, seconds: int) -> AudioFragment: ...

class AudioRecognizer(Protocol):
    async def recognize(self, fragment: AudioFragment) -> RecognitionResult | None: ...

class FingerprintComparer(Protocol):
    async def similarity(self, a: AudioFragment, b: AudioFragment) -> float: ...

class TokenCipher(Protocol): ...

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

- Rate limit: token bucket в Redis на `(platform, account)`.
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
- **`Transfer.user_id`, `LibrarySource.account_id`, `LibraryDestination.account_id` — просто
  `UUID`, без FK.** Модули `identity` (`User`) и `accounts` (`ConnectedAccount`) не построены.
  FK на `users`/`connected_accounts` — отдельной миграцией, когда эти модули появятся.
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
  задвоить работу: `ProcessTransferUseCase`/`MatchTransferItemUseCase`/`ResolveUncertainItemUseCase`
  читают `Transfer` через `TransferRepository.get_for_update()` (`SELECT ... FOR UPDATE` на строке
  `transfers`) — конкурентная доставка блокируется на этом локе до коммита первой попытки, затем
  видит уже изменённый статус и либо ловит `InvalidTransferTransitionError` от доменного метода
  (`ProcessTransferUseCase.start()`), либо явно проверяет статус item'а перед мутацией
  (`MatchTransferItemUseCase`) и выходит no-op.
- **Побочный эффект до commit.** `WriteTransferUseCase.create_playlist` на реальной площадке —
  вызов вовне; если транзакция после него упадёт и откатится, повторная доставка `run_write`
  создаст плейлист повторно (нет саги/outbox). Для фейковой площадки на этом этапе не критично,
  для реальных адаптеров (этап 4) — нужно будет решать.
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

---

## 12. Фронтенд и расширение

- **Feature-Sliced Design** — то же разделение ответственности, что на бэке.
- Экраны: подключение площадок → выбор плейлиста → живой прогресс (SSE) →
  **ревью сомнительных** (кандидаты, обложки, длительность, превью) → история.
- Расширение: привязка к аккаунту одноразовым кодом, WebSocket к бэкенду,
  модули под каждую площадку, host_permissions только на нужные домены.

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
4. **Адаптеры**: Яндекс → SoundCloud → YT Music → VK → Spotify (чтение).
5. **Фронтенд**: подключение, перенос, ревью.
6. **recognition**: ffmpeg, shazamio, ACRCloud, chromaprint.
7. **enrichment**: обложки (Deezer → iTunes → CAA → Genius), ISRC-мост, тексты (Genius API + LRCLIB).
8. **backups**: экспорт во все форматы, импорт из файла, расписания, S3 (SeaweedFS).
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

