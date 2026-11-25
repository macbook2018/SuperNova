from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.catalog import _normalize_base_url, _trim_required

EmbeddingProviderType = Literal["openai_compatible", "openai", "qwen"]


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
    verified_at: datetime | None
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


class KnowledgeOperationUsage(BaseModel):
    operation: str
    status: str
    calls: int
    known_input_tokens: int
    known_output_tokens: int
    unknown_calls: int = Field(description="供应商尚未返回任何 Token 用量的调用次数")


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
    active_release_id: UUID | None = None
    legacy_active_chunks: int = 0
    operation_usage: list[KnowledgeOperationUsage] = Field(
        default_factory=list,
        description="逐次供应商调用观测，按步骤和状态汇总。与上述兼容汇总有重叠，不应相加。",
    )


class IndexBuildConfig(BaseModel):
    model_config = {"extra": "forbid"}

    parser_version: Literal["legacy", "structured_v1"] = "legacy"
    chunk_size: int | None = Field(default=None, ge=100, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0, lt=4000)


class IndexBuildRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_key: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    expected_generation: int = Field(ge=0)
    config: IndexBuildConfig = Field(default_factory=IndexBuildConfig)


class IndexPublishRequest(BaseModel):
    model_config = {"extra": "forbid"}

    expected_generation: int = Field(ge=0)


class IndexRollbackRequest(IndexPublishRequest):
    target_release_id: UUID | None


class IndexStateRead(BaseModel):
    generation: int
    active_release_id: UUID | None


class IndexVersionConfigRead(BaseModel):
    parser_version: str
    chunk_size: int
    chunk_overlap: int


class IndexVersionRead(BaseModel):
    id: UUID
    revision: int
    status: str
    expected_generation: int
    config: IndexVersionConfigRead
    created_at: datetime
    published_at: datetime | None


class IndexVersionsRead(IndexStateRead):
    versions: list[IndexVersionRead]


class DocumentRevisionRead(BaseModel):
    version: int
    created_at: datetime
    chunk_count: int
    profile_origin: str


class DocumentIndexStateRead(BaseModel):
    active_index_version: int
    generation: int


class DocumentHistoryRead(DocumentIndexStateRead):
    revisions: list[DocumentRevisionRead]


class DocumentRestoreRequest(BaseModel):
    model_config = {"extra": "forbid"}

    expected_active_index_version: int = Field(ge=0)
    expected_generation: int = Field(ge=0)
