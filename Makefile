# Команды разработки. Рецепты выполняются в PowerShell (разработка на Windows).
# Список целей: make help
#
# Ручной e2e одной командой (поднимет приложение, применит миграции, перенесёт плейлист):
#   make e2e LINK="https://music.yandex.ru/users/<login>/playlists/<номер>"
#   make e2e LINK="..." ACCEPT=1 TITLE="Мой перенос"   # ACCEPT=1 — принять uncertain и дописать

SHELL := powershell.exe
.SHELLFLAGS := -NoProfile -ExecutionPolicy Bypass -Command
.DEFAULT_GOAL := help

LINK ?=
TITLE ?=
ACCEPT ?= 0
EMAIL ?=
MAX ?= 10
LIBRARY ?= 0

.PHONY: help up down migrate logs ps e2e e2e-extension e2e-soundcloud e2e-yandex e2e-vk test test-integration test-audio test-live lint check ext-install ext-build ext-build-firefox ext-test fe-install fe-dev fe-build fe-test fe-gen-api

help: ## Список команд
	@[Console]::OutputEncoding = [Text.Encoding]::UTF8; Select-String -Path Makefile -Pattern '^([a-z0-9-]+):.*## (.*)$$' | ForEach-Object { '  make {0,-18} {1}' -f $$_.Matches[0].Groups[1].Value, $$_.Matches[0].Groups[2].Value }

up: ## Собрать и поднять всё приложение (docker compose --profile app)
	docker compose --profile app up -d --build

down: ## Остановить приложение
	docker compose --profile app down

migrate: ## Применить миграции в контейнере
	docker compose run --rm api alembic upgrade head

ps: ## Состояние сервисов
	docker compose --profile app ps

logs: ## Логи api и воркеров (Ctrl+C — выход)
	docker compose logs -f api worker worker-extension

e2e: up migrate ## Ручной e2e Яндекс/SoundCloud → новый плейлист Яндекса: make e2e LINK="..." [ACCEPT=1] [TITLE="..."]
	./scripts/e2e-yandex.ps1 -Link '$(LINK)' -Title '$(TITLE)' $(if $(filter 1,$(ACCEPT)),-AcceptUncertain,)

e2e-extension: up migrate ## Сквозной тест расширения: фейковое расширение по WebSocket, браузер «закрывают» посреди записи
	Set-Location backend; uv run python -m tests.tools.fake_extension e2e --api http://localhost:8000

e2e-soundcloud: ## Ручной e2e 4c-3 (расширение привязано): make e2e-soundcloud EMAIL=... LINK="..." [ACCEPT=1] [TITLE="..."]
	./scripts/e2e-soundcloud-extension.ps1 -Email '$(EMAIL)' -Link '$(LINK)' -Title '$(TITLE)' $(if $(filter 1,$(ACCEPT)),-AcceptUncertain,)

e2e-yandex: ## Ручной e2e 4c-4 (лайки SoundCloud → Яндекс через расширение): make e2e-yandex EMAIL=... [LINK="..."] [ACCEPT=1] [TITLE="..."]
	./scripts/e2e-yandex-extension.ps1 -Email '$(EMAIL)' -Link '$(LINK)' -Title '$(TITLE)' $(if $(filter 1,$(ACCEPT)),-AcceptUncertain,)

e2e-vk: ## Ручной e2e 4c-5 (плейлист → VK через расширение): make e2e-vk EMAIL=... LINK="..." [MAX=10|50] [LIBRARY=1] [ACCEPT=1]
	./scripts/e2e-vk-extension.ps1 -Email '$(EMAIL)' -Link '$(LINK)' -Title '$(TITLE)' -MaxTracks $(MAX) $(if $(filter 1,$(LIBRARY)),-ToLibrary,) $(if $(filter 1,$(ACCEPT)),-AcceptUncertain,)

test: ## Unit-тесты и тесты адаптеров без Docker
	Set-Location backend; uv run pytest tests/unit tests/integration/platforms tests/integration/recognizers

test-integration: ## Интеграционные тесты (testcontainers, нужен Docker)
	Set-Location backend; uv run pytest tests/integration

test-audio: ## Аудио-тесты (ffmpeg) — только в контейнере
	docker compose run --rm --no-deps -v "$${PWD}/backend/tests:/app/tests" api pytest -m audio -p no:cacheprovider

test-live: ## Live-тесты на токене из .env (меняют аккаунт — см. tests/live)
	Set-Location backend; uv run pytest -m live -v -s

lint: ## ruff + mypy + import-linter
	Set-Location backend; uv run ruff check .; if ($$?) { uv run ruff format --check . }; if ($$?) { uv run mypy src }; if ($$?) { uv run lint-imports }

ext-install: ## Расширение: установить зависимости (pnpm)
	Set-Location extension; pnpm install

ext-build: ## Расширение: сборка Chrome / Яндекс Браузер → extension/.output/chrome-mv3
	Set-Location extension; pnpm build

ext-build-firefox: ## Расширение: сборка Firefox → extension/.output/firefox-mv3
	Set-Location extension; pnpm build:firefox

ext-test: ## Расширение: типы, формат, vitest
	Set-Location extension; pnpm typecheck; if ($$?) { pnpm format:check }; if ($$?) { pnpm test }

fe-install: ## Фронтенд: установить зависимости (pnpm)
	Set-Location frontend; pnpm install

fe-dev: ## Фронтенд: dev-сервер http://localhost:5173 (/api → localhost:8000)
	Set-Location frontend; pnpm dev

fe-build: ## Фронтенд: сборка → frontend/dist
	Set-Location frontend; pnpm build

fe-test: ## Фронтенд: типы, ESLint (слои FSD), формат, vitest
	Set-Location frontend; pnpm check

fe-gen-api: ## Фронтенд: типы API из OpenAPI бэкенда (без Docker)
	Set-Location frontend; pnpm gen:api

check: lint test ext-test fe-test ## lint + тесты без Docker (бэкенд, расширение, фронтенд)
