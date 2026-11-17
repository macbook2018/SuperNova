from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

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
    embedding_source: Literal["platform", "byok"] = "platform"
    platform_embedding_model_id: UUID | None = None
    embedding_provider_id: UUID | None = None
    embedding_model_name: str | None = Field(default=None, min_length=1, max_length=120)
    embedding_dimensions: Literal[1536] = 1536
    chunk_size: int = Field(default=800, ge=100, le=4000)
    chunk_overlap: int = Field(default=120, ge=0, le=3999)
    retrieval_top_k: int = Field(default=5, ge=1, le=20)
    retrieval_score_threshold: float = Field(default=0.15, ge=0, le=1)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _trim_required(value)

    @field_validator("embedding_model_name")
    @classmethod
    def trim_embedding_model_name(cls, value: str | None) -> str | None:
        return _trim_required(value) if value is not None else None

    @model_validator(mode="after")
    def validate_embedding_source(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        if self.embedding_source == "platform" and self.embedding_provider_id is not None:
            raise ValueError("平台 Embedding 模式不能绑定租户供应商")
        if self.embedding_source == "byok" and self.platform_embedding_model_id is not None:
            raise ValueError("BYOK Embedding 模式不能绑定平台模型")
        if self.embedding_source == "byok" and (
            self.embedding_provider_id is None or self.embedding_model_name is None
        ):
            raise ValueError("BYOK Embedding 模式必须选择供应商并填写 Embedding 模型名称")
        return self


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    embedding_source: Literal["platform", "byok"] | None = None
    platform_embedding_model_id: UUID | None = None
    embedding_provider_id: UUID | None = None
    embedding_model_name: str | None = Field(default=None, min_length=1, max_length=120)
    embedding_dimensions: Literal[1536] | None = None
    chunk_size: int | None = Field(default=None, ge=100, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=3999)
    retrieval_top_k: int | None = Field(default=None, ge=1, le=20)
    retrieval_score_threshold: float | None = Field(default=None, ge=0, le=1)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return _trim_required(value) if value is not None else None

    @field_validator("description")
    @classmethod
    def trim_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("embedding_model_name")
    @classmethod
    def trim_embedding_model_name(cls, value: str | None) -> str | None:
        return _trim_required(value) if value is not None else None

    @model_validator(mode="after")
    def validate_chunk_settings(self):
        if (
            self.chunk_size is not None
            and self.chunk_overlap is not None
            and self.chunk_overlap >= self.chunk_size
        ):
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        return self


class KnowledgeBaseRead(BaseModel):
    id: UUID
    name: str
    description: str
    embedding_source: str = "platform"
    platform_embedding_model_id: UUID | None = None
    embedding_provider_id: UUID | None = None
    embedding_model_name: str | None = None
    embedding_dimensions: int = 1536
    chunk_size: int = 800
    chunk_overlap: int = 120
    retrieval_top_k: int = 5
    retrieval_score_threshold: float = 0.15
    enabled: bool = True
    documents_count: int = 0
    created_at: datetime
    updated_at: datetime


class PersonaCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(default="", max_length=500)
    system_prompt: str = Field(min_length=1, max_length=20000)
    model_source: Literal["platform", "byok"] = "byok"
    provider_id: UUID | None = None
    platform_model_id: UUID | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=120)
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=1.0, ge=0, le=1)
    max_tokens: int = Field(default=2048, ge=1, le=65536)
    enabled: bool = True
    knowledge_base_ids: list[UUID] = Field(default_factory=list)

    @field_validator("slug", mode="before")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("name", "slug", "system_prompt")
    @classmethod
    def trim_required(cls, value: str) -> str:
        return _trim_required(value)

    @field_validator("model_name")
    @classmethod
    def trim_model_name(cls, value: str | None) -> str | None:
        return _trim_required(value) if value is not None else None

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
    model_source: Literal["platform", "byok"] | None = None
    provider_id: UUID | None = None
    platform_model_id: UUID | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=120)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1, le=65536)
    enabled: bool | None = None
    knowledge_base_ids: list[UUID] | None = None

    @field_validator("slug", mode="before")
    @classmethod
    def normalize_slug(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("name", "slug", "system_prompt")
    @classmethod
    def trim_required(cls, value: str | None) -> str | None:
        return _trim_required(value) if value is not None else None

    @field_validator("model_name")
    @classmethod
    def trim_model_name(cls, value: str | None) -> str | None:
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
    model_source: str
    provider_id: UUID | None
    provider_name: str | None
    platform_model_id: UUID | None
    platform_model_name: str | None
    model_name: str
    temperature: float
    top_p: float
    max_tokens: int
    enabled: bool
    knowledge_base_ids: list[UUID]
    created_at: datetime
    updated_at: datetime
