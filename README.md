# SyncPlaylists

Перенос плейлистов и медиатек между **Яндекс Музыкой, VK Музыкой, SoundCloud, Spotify и
YouTube Music**. Треки сопоставляются по ISRC, по названию (нормализация и нечёткое сравнение),
а со временем — и по звуку. Сомнительные совпадения пользователь подтверждает сам.

> Статус: в разработке. Сейчас работают Яндекс Музыка, VK и SoundCloud (через браузерное
> расширение); Spotify и YouTube Music — на следующих этапах. План и решения —
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), раздел 14.

## Как это работает

1. Вставляете ссылку на плейлист (или выбираете «Мою медиатеку») — сайт показывает, что по ней
   найдено: площадку, название, число треков.
2. Выбираете, куда перенести: новый плейлист, свой существующий или медиатеку.
3. Сервис читает источник, ищет каждый трек на другой площадке и показывает прогресс вживую.
4. Сомнительные и ненайденные — на ревью: кандидаты с обложкой и длительностью,
   «Выбрать» / «Пропустить».
5. «Записать» — треки добавляются в том же порядке и без дублей. В конце — отчёт.

Операции на площадках выполняет **браузерное расширение** в вашей сессии: пароли от площадок
сервис не видит и не хранит. Если браузер закрыли, перенос встаёт на паузу и продолжается сам,
когда браузер снова открыт. Лимиты площадок (например, дневной лимит «Моей музыки» VK)
выдерживаются автоматически: перенос ждёт и продолжает сам.

## Из чего состоит

| Часть | Что внутри | Где |
|---|---|---|
| Бэкенд | Python 3.12, FastAPI, SQLAlchemy 2 (async) + PostgreSQL 17, Redis + ARQ (очереди), SSE, dishka (DI) | [`backend/`](backend/) |
| Фронтенд | React 19, Vite, TypeScript, TanStack Router/Query, Tailwind, Feature-Sliced Design | [`frontend/`](frontend/) |
| Расширение | WXT, Manifest V3 — Chrome, Яндекс Браузер, Firefox | [`extension/`](extension/) |
| Инфраструктура | Docker Compose: postgres, redis, s3 (SeaweedFS), api, воркеры, frontend (nginx) | [`docker-compose.yml`](docker-compose.yml) |

Бэкенд — модульный монолит по Clean Architecture + DDD: `backend/src/syncplaylists/modules/<контекст>/{domain,application,infrastructure,presentation}`;
слои и независимость контекстов проверяет `import-linter`. Подробно — в
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Быстрый старт (Docker)

Нужны Docker Desktop (Windows/macOS) или Docker Engine, а для расширения — Node 24 и pnpm.

```bash
cp .env.example .env
```

В `.env` задайте `SECURITY__TOKEN_ENCRYPTION_KEY` — base64 от 32 случайных байт:

```bash
python -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

Поднять всё приложение и применить миграции:

```bash
docker compose --profile app up -d --build
```

```bash
docker compose run --rm api alembic upgrade head
```

Готово:

- сайт — http://localhost:5173
- API — http://localhost:8000 (документация OpenAPI — http://localhost:8000/docs)

Без `--profile app` поднимается только инфраструктура (postgres, redis, s3) — так удобно
запускать бэкенд и фронтенд на хосте.

### Расширение

```bash
cp extension/.env.example extension/.env
```

```bash
make ext-install
```

```bash
make ext-build
```

Сборка появится в `extension/.output/chrome-mv3` (для Firefox — `make ext-build-firefox` →
`firefox-mv3`). Установка: `chrome://extensions` (в Яндекс Браузере — `browser://extensions`) →
«Режим разработчика» → «Загрузить распакованное». Затем на сайте откройте «Подключения», а в
расширении — код привязки, и введите его на сайте. Площадки включаются в самом расширении.

## Разработка

Команды собраны в [`Makefile`](Makefile) (рецепты — PowerShell, разработка идёт на Windows);
полный список — `make help`.

| Задача | Команда |
|---|---|
| Бэкенд: зависимости | `cd backend && uv sync` |
| Только инфраструктура в Docker | `docker compose up -d` |
| Фронтенд: dev-сервер (http://localhost:5173, `/api` → `localhost:8000`) | `make fe-install`, `make fe-dev` |
| Типы API для фронта из OpenAPI бэкенда | `make fe-gen-api` |
| Миграция после изменения ORM | `cd backend && uv run alembic revision --autogenerate -m "..."` |
| Логи api и воркеров | `make logs` |

Dev-сервер фронта слушает все сетевые интерфейсы — вёрстку можно проверить с телефона в той
же сети по адресу компьютера и порту 5173.

## Проверки

| Что | Команда |
|---|---|
| Линт и типы бэкенда (ruff, mypy --strict, import-linter) | `make lint` |
| Unit-тесты бэкенда и тесты адаптеров | `make test` |
| Интеграционные тесты (Postgres и Redis в testcontainers) — **только с хоста**, нужен запущенный Docker | `make test-integration` |
| Фронтенд: типы, ESLint (в т.ч. слои FSD), формат, vitest | `make fe-test` |
| Расширение: типы, формат, vitest | `make ext-test` |
| Всё без Docker | `make check` |
| Сквозной тест расширения (фейковое расширение по WebSocket) | `make e2e-extension` |

Ручные сквозные прогоны с настоящим расширением и аккаунтами площадок — `make e2e-soundcloud`,
`make e2e-yandex`, `make e2e-vk` (параметры — в `make help`).

## Принципы, которые держим

- Пароли пользователей площадок не принимаем и не храним; токены — только зашифрованными
  (AES-GCM).
- Расширение отправляет на сервер только данные операции — без cookie и токенов страницы
  (проверяется тестами расширения).
- Аудио-фрагменты не хранятся дольше распознавания; тексты песен не парсятся со страниц Genius.
- Настройки — только через переменные окружения (`.env`, шаблон — [`.env.example`](.env.example)).

## Документация

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — архитектура, решения, этапы, известные долги.
- [`docs/EXTENSION_STORE.md`](docs/EXTENSION_STORE.md), [`docs/EXTENSION_PRIVACY.md`](docs/EXTENSION_PRIVACY.md) — расширение: разрешения, публикация, конфиденциальность.
- [`docs/YANDEX_TOKEN.md`](docs/YANDEX_TOKEN.md), [`docs/SOUNDCLOUD_TOKEN.md`](docs/SOUNDCLOUD_TOKEN.md) — запасные способы подключения по токену.
- [`CLAUDE.md`](CLAUDE.md) — правила разработки для ИИ-ассистента (подходят и людям).
