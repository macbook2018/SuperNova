from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_JWT_SECRET = "local-development-only-secret-change-before-deploy-2026"
_DEFAULT_ENCRYPTION_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SUPERNOVA_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "SuperNova API"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = (
        "postgresql://supernova:change-me-local-only@localhost:5432/supernova"
    )
    redis_url: str = "redis://:change-me-local-only@localhost:6379/0"
    jwt_secret: str = _DEFAULT_JWT_SECRET
    encryption_key: str | None = _DEFAULT_ENCRYPTION_KEY
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    platform_admin_emails: str = ""
    platform_credentials_json: str = "{}"

    @property
    def platform_admin_email_set(self) -> frozenset[str]:
        return frozenset(
            email.strip().lower()
            for email in self.platform_admin_emails.split(",")
            if email.strip()
        )

    @model_validator(mode="after")
    def validate_secrets_and_ttls(self) -> "Settings":
        production = self.environment.lower() in {"production", "prod"}
        if production and (
            self.jwt_secret == _DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32
        ):
            raise ValueError(
                "SUPERNOVA_JWT_SECRET must be a unique secret of at least 32 characters"
            )
        if production and (
            not self.encryption_key or self.encryption_key == _DEFAULT_ENCRYPTION_KEY
        ):
            raise ValueError(
                "SUPERNOVA_ENCRYPTION_KEY must be a unique Fernet key in production"
            )
        if self.encryption_key:
            try:
                Fernet(self.encryption_key.encode("ascii"))
            except (ValueError, UnicodeEncodeError):
                raise ValueError("SUPERNOVA_ENCRYPTION_KEY must be a valid Fernet key") from None
        if self.access_token_ttl_minutes < 5 or self.access_token_ttl_minutes > 60:
            raise ValueError("SUPERNOVA_ACCESS_TOKEN_TTL_MINUTES must be between 5 and 60")
        if self.refresh_token_ttl_days < 1 or self.refresh_token_ttl_days > 90:
            raise ValueError("SUPERNOVA_REFRESH_TOKEN_TTL_DAYS must be between 1 and 90")
        return self

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
