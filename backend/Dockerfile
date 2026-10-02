# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Зависимости кэшируются отдельно от кода приложения. Прод-слой без dev-группы
# (pytest, testcontainers, ...) — она только в builder-dev/dev ниже.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ src/
RUN uv sync --frozen --no-dev


FROM builder AS builder-dev

RUN uv sync --frozen --no-install-project
RUN uv sync --frozen


FROM python:3.12-slim-bookworm AS base

# ffmpeg + chromaprint (fpcalc) нужны для распознавания аудио (этап 6);
# один образ используется для ролей api/worker/worker-recognize.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ffmpeg \
        libchromaprint-tools \
        curl \
        procps \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000


FROM base AS dev

# Dev-группа (pytest, testcontainers, ruff, mypy, ...) + tests/ — только здесь.
# Используется через docker-compose.override.yml (build.target: dev), в т.ч. для
# "docker compose run --rm api pytest tests/integration".
COPY --from=builder-dev /app/.venv /app/.venv
COPY src/ src/
COPY tests/ tests/
COPY alembic.ini pyproject.toml ./
COPY migrations/ migrations/

USER app

CMD ["uvicorn", "syncplaylists.bootstrap.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]


# prod — последний стейдж: "docker build ." без --target собирает именно его.
FROM base AS prod

COPY --from=builder /app/.venv /app/.venv
COPY src/ src/
COPY alembic.ini ./
COPY migrations/ migrations/

USER app

CMD ["uvicorn", "syncplaylists.bootstrap.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
