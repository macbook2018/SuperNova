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
    runtime_jwt_secret: str | None = None
    runtime_gateway_base_url: str = "http://host.docker.internal:8001/internal/runtime/v1"
    runtime_data_root: str = "/tmp/supernova-runtime"
    astrbot_runtime_image: str = "macbook201808/supernova-astrbot-runtime:4.29.4-6133541"
    runtime_memory_limit: str = "512m"
    runtime_cpu_limit: float = 1.0
    runtime_pids_limit: int = 128
    runtime_worker_poll_seconds: float = 2.0
    runtime_worker_lease_seconds: int = 180
    astrbot_runtime_version: str = "4.29.4"
    astrbot_runtime_commit: str = "61335414433db721b3f7ecc93f90deafe0a59d78"
    encryption_key: str | None = _DEFAULT_ENCRYPTION_KEY
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    platform_admin_emails: str = ""
    platform_credentials_json: str = "{}"
    s3_endpoint_url: str = "http://minio:9000"
    s3_access_key: str = "supernova"
    s3_secret_key: str = "change-me-local-only"
    s3_bucket: str = "supernova-knowledge"
    s3_region: str = "us-east-1"
    knowledge_max_upload_bytes: int = 25 * 1024 * 1024
    knowledge_max_url_bytes: int = 20 * 1024 * 1024
    knowledge_worker_poll_seconds: float = 2.0
    knowledge_worker_lease_seconds: int = 180
    embedding_timeout_seconds: float = 60.0
    chat_import_max_file_bytes: int = 50 * 1024 * 1024
    chat_import_max_batch_bytes: int = 100 * 1024 * 1024
    chat_import_max_files: int = 20
    chat_import_worker_poll_seconds: float = 2.0
    chat_import_worker_lease_seconds: int = 180
    chat_import_batch_size: int = 500
    chat_import_temp_ttl_hours: int = 24

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
        if not 0.2 <= self.runtime_worker_poll_seconds <= 60:
            raise ValueError("SUPERNOVA_RUNTIME_WORKER_POLL_SECONDS must be between 0.2 and 60")
        if not 30 <= self.runtime_worker_lease_seconds <= 3600:
            raise ValueError("SUPERNOVA_RUNTIME_WORKER_LEASE_SECONDS must be between 30 and 3600")
        if self.runtime_cpu_limit <= 0 or self.runtime_cpu_limit > 8:
            raise ValueError("SUPERNOVA_RUNTIME_CPU_LIMIT must be greater than 0 and at most 8")
        if not 16 <= self.runtime_pids_limit <= 4096:
            raise ValueError("SUPERNOVA_RUNTIME_PIDS_LIMIT must be between 16 and 4096")
        if self.access_token_ttl_minutes < 5 or self.access_token_ttl_minutes > 60:
            raise ValueError("SUPERNOVA_ACCESS_TOKEN_TTL_MINUTES must be between 5 and 60")
        if self.knowledge_max_upload_bytes < 1024 or self.knowledge_max_url_bytes < 1024:
            raise ValueError("Knowledge ingestion size limits must be at least 1 KiB")
        if not 0.2 <= self.knowledge_worker_poll_seconds <= 60:
            raise ValueError("SUPERNOVA_KNOWLEDGE_WORKER_POLL_SECONDS must be between 0.2 and 60")
        if not 30 <= self.knowledge_worker_lease_seconds <= 3600:
            raise ValueError("SUPERNOVA_KNOWLEDGE_WORKER_LEASE_SECONDS must be between 30 and 3600")
        if not 1024 <= self.chat_import_max_file_bytes <= 250 * 1024 * 1024:
            raise ValueError(
                "SUPERNOVA_CHAT_IMPORT_MAX_FILE_BYTES must be between 1 KiB and 250 MiB"
            )
        if not 1024 <= self.chat_import_max_batch_bytes <= 500 * 1024 * 1024:
            raise ValueError(
                "SUPERNOVA_CHAT_IMPORT_MAX_BATCH_BYTES must be between 1 KiB and 500 MiB"
            )
        if not 1 <= self.chat_import_max_files <= 100:
            raise ValueError("SUPERNOVA_CHAT_IMPORT_MAX_FILES must be between 1 and 100")
        if not 0.2 <= self.chat_import_worker_poll_seconds <= 60:
            raise ValueError("SUPERNOVA_CHAT_IMPORT_WORKER_POLL_SECONDS must be between 0.2 and 60")
        if not 30 <= self.chat_import_worker_lease_seconds <= 3600:
            raise ValueError(
                "SUPERNOVA_CHAT_IMPORT_WORKER_LEASE_SECONDS must be between 30 and 3600"
            )
        if not 1 <= self.chat_import_batch_size <= 5000:
            raise ValueError("SUPERNOVA_CHAT_IMPORT_BATCH_SIZE must be between 1 and 5000")
        if not 1 <= self.chat_import_temp_ttl_hours <= 72:
            raise ValueError("SUPERNOVA_CHAT_IMPORT_TEMP_TTL_HOURS must be between 1 and 72")
        if self.refresh_token_ttl_days < 1 or self.refresh_token_ttl_days > 90:
            raise ValueError("SUPERNOVA_REFRESH_TOKEN_TTL_DAYS must be between 1 and 90")
        return self

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
