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

.PHONY: help up down migrate logs ps e2e test test-integration test-live lint check

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

logs: ## Логи api и worker (Ctrl+C — выход)
	docker compose logs -f api worker

e2e: up migrate ## Ручной e2e Яндекс → новый плейлист: make e2e LINK="..." [ACCEPT=1] [TITLE="..."]
	./scripts/e2e-yandex.ps1 -Link '$(LINK)' -Title '$(TITLE)' $(if $(filter 1,$(ACCEPT)),-AcceptUncertain,)

test: ## Unit-тесты и тесты адаптеров без Docker
	Set-Location backend; uv run pytest tests/unit tests/integration/platforms

test-integration: ## Интеграционные тесты (testcontainers, нужен Docker)
	Set-Location backend; uv run pytest tests/integration

test-live: ## Live-тесты на токене из .env (меняют аккаунт — см. tests/live)
	Set-Location backend; uv run pytest -m live -v -s

lint: ## ruff + mypy + import-linter
	Set-Location backend; uv run ruff check .; if ($$?) { uv run ruff format --check . }; if ($$?) { uv run mypy src }; if ($$?) { uv run lint-imports }

check: lint test ## lint + тесты без Docker
