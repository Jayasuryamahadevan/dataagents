from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DATAAGENTS_")

    database_path: str = "./dataagents.db"
    api_key: str | None = None
    request_timeout_seconds: float = 30
    max_records_per_sync: int = 10_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
