from pydantic import BaseModel, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    dsn: PostgresDsn
    pool_size: int = 10
    echo: bool = False


class RedisSettings(BaseModel):
    dsn: RedisDsn


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # ".env" — если команды запускаются из backend/ с собственным .env;
        # "../.env" — единый .env в корне репозитория (там же, где docker-compose.yml).
        env_file=(".env", "../.env"),
        env_nested_delimiter="__",
        extra="ignore",
    )

    env: str = "dev"
    db: DatabaseSettings
    redis: RedisSettings
