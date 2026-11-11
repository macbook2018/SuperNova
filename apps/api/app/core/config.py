from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SUPERNOVA_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "SuperNova API"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql://supernova:supernova_dev_only@localhost:5432/supernova"
    redis_url: str = "redis://localhost:6379/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
