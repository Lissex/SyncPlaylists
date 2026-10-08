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

.PHONY: help up down migrate logs ps e2e e2e-extension test test-integration test-live lint check ext-install ext-build ext-build-firefox ext-test

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

test: ## Unit-тесты и тесты адаптеров без Docker
	Set-Location backend; uv run pytest tests/unit tests/integration/platforms

test-integration: ## Интеграционные тесты (testcontainers, нужен Docker)
	Set-Location backend; uv run pytest tests/integration

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

check: lint test ext-test ## lint + тесты без Docker (бэкенд и расширение)
