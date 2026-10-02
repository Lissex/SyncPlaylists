# SyncPlaylists

Сервис переноса плейлистов между Spotify, Яндекс Музыкой, VK Музыкой, SoundCloud и YouTube Music
с сопоставлением треков по ISRC, тексту и звуку.

**Полная архитектура: `docs/ARCHITECTURE.md` — прочитай перед любой задачей.**

## Стек
- Backend: Python 3.12, FastAPI, pydantic-settings, dishka (DI), SQLAlchemy 2.0 async + asyncpg, Alembic,
  PostgreSQL 17, Redis + ARQ, httpx, Authlib, rapidfuzz, ffmpeg, shazamio, ACRCloud, chromaprint,
  MinIO/S3; обогащение: Genius API, LRCLIB, Deezer API, iTunes Search API, Cover Art Archive.
- Frontend: React 19 + Vite + TS, TanStack Query/Router, Tailwind + shadcn/ui, Feature-Sliced Design.
- Extension: WXT + TS, Manifest V3.
- Инструменты: uv, ruff, mypy --strict, pytest, testcontainers, respx, import-linter, pre-commit.

## Архитектурные правила (обязательны)
- Clean Architecture + DDD, модульный монолит: `backend/src/syncplaylists/modules/<context>/{domain,application,infrastructure,presentation}`.
- Зависимости только внутрь. `domain` не импортирует FastAPI, SQLAlchemy, Redis, httpx, settings.
- Контексты не импортируют `domain` друг друга — только application-интерфейсы или доменные события.
- Внешние системы — через порты (`typing.Protocol`) в application; реализации в `integrations/` или `infrastructure/`.
- ORM-модели отделены от доменных сущностей, преобразование в `mappers.py`.
- Настройки — только через `Settings` (pydantic-settings) в `infrastructure/config`, раздаются через DI.
  Секреты — `SecretStr`. Никаких `os.getenv` в коде.
- Сборка зависимостей — только в `bootstrap/container.py`.
- Пароли пользователей площадок никогда не храним. Токены — только зашифрованными (AES-GCM).
- Аудио-фрагменты не сохраняем на диск дольше распознавания.
- Тексты песен не парсим со страниц Genius и не храним в Postgres — только через `LyricsProvider`, кэш с TTL.

## Docker (обязательно)
Проект работает в Docker. Разработка идёт на Windows с Docker Desktop.
- **Инфраструктура только в Docker**: PostgreSQL 17, Redis 7, MinIO. Ничего из этого не ставим на хост.
- **У каждого приложения свой Dockerfile**: `backend/Dockerfile` (multi-stage на uv; в образе есть
  ffmpeg и chromaprint (`libchromaprint-tools`)) — один образ для `api`, `worker` и `worker-recognize`;
  `frontend/Dockerfile` (сборка Vite → nginx).
- **`docker-compose.yml` в корне** описывает сервисы `postgres`, `redis`, `minio`, `api`, `worker`,
  `worker-recognize`, `frontend`. У каждого есть healthcheck, данные лежат в именованных volumes.
- **Профили compose**: по умолчанию поднимается только инфраструктура; `--profile app` поднимает всё приложение.
- **Аудио-инструменты** (ffmpeg, chromaprint, shazamio) запускаем и тестируем **только в контейнере**,
  а не на Windows-хосте.
- **Конфиг только через env.** `.env` (не в git) и `.env.example` (в git). Внутри compose сервисы
  обращаются друг к другу по имени (`postgres`, `redis`), с хоста — через `localhost`.
- **`docker-compose.override.yml`** для dev: монтирование исходников и `--reload`.
- **Тесты** интеграционных слоёв ходят в Postgres и Redis через testcontainers или через compose-сервисы.

## Команды
- Установка: `cd backend && uv sync`
- Тесты: `uv run pytest`
- Линт/типы: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run lint-imports`
- Миграции: `uv run alembic revision --autogenerate -m "..."`, `uv run alembic upgrade head`
- Инфраструктура: `docker compose up -d` (postgres, redis, minio)
- Всё приложение: `docker compose --profile app up -d --build`
- Логи: `docker compose logs -f api worker`
- Миграции в контейнере: `docker compose run --rm api alembic upgrade head`
- Тесты в контейнере (нужно для аудио): `docker compose run --rm api pytest`

## Как работаем
- Работаем по этапам из раздела 14 `docs/ARCHITECTURE.md`, один этап — одна ветка/PR.
- Перед реализацией этапа — план, затем код, затем тесты; после — все проверки зелёные.
- Новый функционал домена — сначала unit-тест.
- Изменил архитектурное решение — обнови `docs/ARCHITECTURE.md`.
- Язык общения и комментариев к коммитам — русский; идентификаторы в коде — английский.
