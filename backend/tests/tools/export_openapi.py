"""OpenAPI-схема API в файл — для генерации типов фронтенда (frontend: `pnpm gen:api`).

Без Postgres и Redis: приложение только собирается, запросов нет. Настройки — фиктивные,
как в тестах; dev-инструменты расширения выключены, поэтому их маршрутов в схеме нет.

    uv run python -m tests.tools.export_openapi ../frontend/openapi.json
"""

import json
import sys
from pathlib import Path

from syncplaylists.bootstrap.api import create_app
from syncplaylists.infrastructure.config.settings import Settings
from tests.conftest import TEST_SECURITY


def main(out: Path) -> None:
    settings = Settings(
        db={"dsn": "postgresql+asyncpg://openapi:openapi@localhost:5432/openapi"},
        redis={"dsn": "redis://localhost:6379/0"},
        security=TEST_SECURITY,
        extension={"dev_page_enabled": False, "diagnostics_enabled": False},
    )
    schema = create_app(settings).openapi()
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OpenAPI: {len(schema['paths'])} путей: {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json"))
