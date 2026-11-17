from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.catalog import _normalize_base_url, _trim_required

EmbeddingProviderType = Literal["openai_compatible", "openai", "qwen", "deepseek"]


class PlatformEmbeddingModelCreate(BaseModel):
    code: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    display_name: str = Field(min_length=1, max_length=100)
    provider_type: EmbeddingProviderType
    upstream_model_name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=2048)
    credential_ref: str = Field(min_length=1, max_length=255)
    dimensions: Literal[1536] = 1536
    enabled: bool = False

    @field_validator("display_name", "upstream_model_name", "credential_ref")
    @classmethod
    def trim_required(cls, value: str) -> str:
        return _trim_required(value)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _normalize_base_url(value)


class PlatformEmbeddingModelUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    provider_type: EmbeddingProviderType | None = None
    upstream_model_name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    credential_ref: str | None = Field(default=None, min_length=1, max_length=255)
    dimensions: Literal[1536] | None = None
    enabled: bool | None = None

    @field_validator("display_name", "upstream_model_name", "credential_ref")
    @classmethod
    def trim_optional(cls, value: str | None) -> str | None:
        return _trim_required(value) if value is not None else None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        return _normalize_base_url(value) if value is not None else None


class PlatformEmbeddingModelAdminRead(BaseModel):
    id: UUID
    code: str
    display_name: str
    provider_type: str
    upstream_model_name: str
    base_url: str
    credential_ref: str
    dimensions: int
    enabled: bool
    created_at: datetime


class PlatformEmbeddingModelPublicRead(BaseModel):
    id: UUID
    code: str
    display_name: str
    model_name: str
    dimensions: int


class KnowledgeDocumentRead(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    original_filename: str
    source_type: Literal["upload", "url"]
    source_url: str | None
    mime_type: str
    file_size_bytes: int
    sha256: str | None
    status: str
    active_index_version: int
    chunk_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeImportUrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)

    @field_validator("url")
    @classmethod
    def validate_public_url_shape(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("支持 HTTP/HTTPS URL；公网网页必须使用 HTTPS")
        if parsed.username or parsed.password:
            raise ValueError("URL 不允许包含用户名或密码")
        if parsed.fragment:
            value = value.split("#", 1)[0]
        return value


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    score_threshold: float | None = Field(default=None, ge=0, le=1)
    document_ids: list[UUID] = Field(default_factory=list)


class KnowledgeSearchHit(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_name: str
    source_type: str
    source_url: str | None
    page_number: int | None
    sheet_name: str | None
    slide_number: int | None
    chunk_index: int
    score: float
    vector_rank: int | None
    lexical_rank: int | None
    snippet: str
    metadata: dict


class KnowledgeSearchResponse(BaseModel):
    query: str
    knowledge_base_id: UUID
    hits: list[KnowledgeSearchHit]


class KnowledgeUsageSummary(BaseModel):
    document_count: int
    ready_documents: int
    queued_documents: int
    failed_documents: int
    storage_bytes: int
    active_chunks: int
    embedding_tokens: int
    embedding_requests: int
    retrieval_requests: int
