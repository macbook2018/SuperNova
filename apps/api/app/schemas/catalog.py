from datetime import datetime
from typing import Literal
from uuid import UUID
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


ProviderType = Literal["openai_compatible", "openai", "deepseek", "qwen", "anthropic"]


def _trim_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("此字段不能为空")
    return value


def _normalize_base_url(value: str) -> str:
    value = _trim_required(value)
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("Base URL 必须是有效的 HTTP 或 HTTPS 地址")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("非本机地址必须使用 HTTPS")
    return value.rstrip("/")


class ModelProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider_type: ProviderType = "openai_compatible"
    base_url: str = Field(min_length=8, max_length=2048)
    api_key: str = Field(min_length=1, max_length=4096)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _trim_required(value)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _normalize_base_url(value)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        return _trim_required(value)


class ModelProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    provider_type: ProviderType | None = None
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    clear_api_key: bool = False
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return _trim_required(value) if value is not None else None

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return _normalize_base_url(value) if value is not None else None

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str | None) -> str | None:
        return _trim_required(value) if value is not None else None


class ModelProviderRead(BaseModel):
    id: UUID
    name: str
    provider_type: ProviderType
    base_url: str
    enabled: bool
    has_api_key: bool
    api_key_hint: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _trim_required(value)


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return _trim_required(value) if value is not None else None

    @field_validator("description")
    @classmethod
    def trim_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class KnowledgeBaseRead(BaseModel):
    id: UUID
    name: str
    description: str
    documents_count: int = 0
    created_at: datetime
    updated_at: datetime


class PersonaCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(default="", max_length=500)
    system_prompt: str = Field(min_length=1, max_length=20000)
    provider_id: UUID | None = None
    model_name: str = Field(min_length=1, max_length=120)
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=1.0, ge=0, le=1)
    max_tokens: int = Field(default=2048, ge=1, le=65536)
    enabled: bool = True
    knowledge_base_ids: list[UUID] = Field(default_factory=list)

    @field_validator("name", "slug", "system_prompt", "model_name")
    @classmethod
    def trim_required(cls, value: str) -> str:
        return _trim_required(value)

    @field_validator("description")
    @classmethod
    def trim_description(cls, value: str) -> str:
        return value.strip()


class PersonaUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    description: str | None = Field(default=None, max_length=500)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    provider_id: UUID | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=120)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1, le=65536)
    enabled: bool | None = None
    knowledge_base_ids: list[UUID] | None = None

    @field_validator("name", "slug", "system_prompt", "model_name")
    @classmethod
    def trim_required(cls, value: str | None) -> str | None:
        return _trim_required(value) if value is not None else None

    @field_validator("description")
    @classmethod
    def trim_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class PersonaRead(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str
    system_prompt: str
    provider_id: UUID | None
    provider_name: str | None
    model_name: str
    temperature: float
    top_p: float
    max_tokens: int
    enabled: bool
    knowledge_base_ids: list[UUID]
    created_at: datetime
    updated_at: datetime
