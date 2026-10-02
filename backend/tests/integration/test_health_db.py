from httpx import ASGITransport, AsyncClient
from testcontainers.community.postgres import PostgresContainer

from syncplaylists.bootstrap.api import create_app
from syncplaylists.infrastructure.config.settings import Settings


async def test_health_db_ok() -> None:
    with PostgresContainer(image="postgres:17", driver="asyncpg") as postgres:
        settings = Settings(
            db={"dsn": postgres.get_connection_url()},
            redis={"dsn": "redis://localhost:6379/0"},
        )
        app = create_app(settings)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health/db")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
