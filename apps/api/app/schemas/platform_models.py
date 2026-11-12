from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.catalog import _normalize_base_url

PlatformProviderType = Literal["openai_compatible", "openai", "deepseek", "qwen", "anthropic"]


class PlatformModelCreate(BaseModel):
    code: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    display_name: str = Field(min_length=1, max_length=100)
    provider_type: PlatformProviderType
    upstream_model_name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=2048)
    credential_ref: str = Field(min_length=1, max_length=255)
    enabled: bool = False

    @field_validator("display_name", "upstream_model_name", "credential_ref")
    @classmethod
    def trim_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("此字段不能为空")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _normalize_base_url(value)


class PlatformModelUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    provider_type: PlatformProviderType | None = None
    upstream_model_name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    credential_ref: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None

    @field_validator("display_name", "upstream_model_name", "credential_ref")
    @classmethod
    def trim_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("此字段不能为空")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        return _normalize_base_url(value) if value is not None else None


class PlatformModelAdminRead(BaseModel):
    id: UUID
    code: str
    display_name: str
    provider_type: str
    upstream_model_name: str
    base_url: str
    credential_ref: str | None
    enabled: bool
    created_at: datetime


class PlatformModelPublicRead(BaseModel):
    id: UUID
    code: str
    display_name: str
    provider_type: str
    model_name: str
    currency: str
    input_per_million_minor: Decimal
    output_per_million_minor: Decimal
    price_revision: int


class PlatformModelPriceCreate(BaseModel):
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    input_per_million_minor: Decimal = Field(ge=0, max_digits=24, decimal_places=8)
    output_per_million_minor: Decimal = Field(ge=0, max_digits=24, decimal_places=8)
    effective_from: datetime
    effective_until: datetime | None = None

    @model_validator(mode="after")
    def validate_period(self):
        if self.effective_from.tzinfo is None:
            raise ValueError("effective_from 必须包含时区")
        if self.effective_until is not None:
            if self.effective_until.tzinfo is None:
                raise ValueError("effective_until 必须包含时区")
            if self.effective_until <= self.effective_from:
                raise ValueError("价格生效结束时间必须晚于开始时间")
        return self


class PlatformModelPriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    platform_model_id: UUID
    revision: int
    currency: str
    input_per_million_minor: Decimal
    output_per_million_minor: Decimal
    effective_from: datetime
    effective_until: datetime | None
    created_at: datetime
