from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UsageTotals(BaseModel):
    requests: int
    successful_requests: int
    failed_requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    unmetered_requests: int


class UsageSummary(BaseModel):
    month_start: datetime
    current_month: UsageTotals
    all_time: UsageTotals


class UsageRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    request_id: str
    persona_slug: str
    provider_type: str
    model_name: str
    model_source: str = "byok"
    request_type: str = "chat"
    platform_model_id: UUID | None = None
    status: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    error_code: str | None
    created_at: datetime
