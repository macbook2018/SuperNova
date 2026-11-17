from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class ChatAnalysisStart(BaseModel):
    model_source: Literal["platform", "byok"] = "platform"
    platform_model_id: UUID | None = None
    provider_id: UUID | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("model_name")
    @classmethod
    def trim_model_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_model_reference(self):
        if self.model_source == "platform":
            if (
                self.platform_model_id is None
                or self.provider_id is not None
                or self.model_name is not None
            ):
                raise ValueError("平台模型模式必须且只能选择一个平台模型")
        else:
            if (
                self.provider_id is None
                or self.platform_model_id is not None
                or not self.model_name
            ):
                raise ValueError("BYOK 模式必须选择供应商并填写模型名称")
        return self


class ChatAnalysisJobRead(BaseModel):
    id: UUID
    import_job_id: UUID
    source_account_id: UUID
    model_source: str
    model_name: str
    status: str
    attempts: int
    owner_message_count: int
    faq_candidate_count: int
    input_tokens: int
    output_tokens: int
    request_count: int
    last_error: str | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PersonaDraftRead(BaseModel):
    id: UUID
    source_account_id: UUID
    analysis_job_id: UUID
    status: str
    summary: str
    profile_json: dict
    system_prompt_suggestion: str
    evidence_message_count: int
    applied_persona_id: UUID | None
    approved_at: datetime | None
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PersonaDraftUpdate(BaseModel):
    summary: str | None = Field(default=None, min_length=1, max_length=4000)
    profile_json: dict | None = None
    system_prompt_suggestion: str | None = Field(default=None, min_length=1, max_length=20000)

    @field_validator("summary", "system_prompt_suggestion")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个需要修改的字段")
        return self


class PersonaDraftApply(BaseModel):
    persona_id: UUID
    mode: Literal["append", "replace"]
    expected_updated_at: datetime


class FaqCandidateRead(BaseModel):
    id: UUID
    source_account_id: UUID
    analysis_job_id: UUID
    status: str
    title: str
    category: str
    canonical_question: str
    question_variants: list[str]
    answer: str
    confidence: float
    occurrence_count: int
    effective_date: str | None
    published_document_id: UUID | None
    approved_at: datetime | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class FaqCandidateUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    category: str | None = Field(default=None, min_length=1, max_length=60)
    canonical_question: str | None = Field(default=None, min_length=1, max_length=2000)
    question_variants: list[str] | None = Field(default=None, max_length=20)
    answer: str | None = Field(default=None, min_length=1, max_length=10000)
    effective_date: str | None = Field(default=None, max_length=32)

    @field_validator("title", "category", "canonical_question", "answer", "effective_date")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("question_variants")
    @classmethod
    def clean_variants(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        cleaned = list(dict.fromkeys(item.strip() for item in values if item.strip()))
        return cleaned[:20]


class ReviewDecision(BaseModel):
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def trim_note(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class FaqPublishRequest(BaseModel):
    knowledge_base_id: UUID


class ChatAnalysisBundle(BaseModel):
    analysis_job: ChatAnalysisJobRead | None
    persona_draft: PersonaDraftRead | None
    faq_candidates: list[FaqCandidateRead]
