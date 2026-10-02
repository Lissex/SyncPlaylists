# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Зависимости кэшируются отдельно от кода приложения.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ src/
RUN uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm AS runtime

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

COPY --from=builder /app/.venv /app/.venv
COPY src/ src/
COPY alembic.ini ./
COPY migrations/ migrations/

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

EXPOSE 8000

CMD ["uvicorn", "syncplaylists.bootstrap.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
