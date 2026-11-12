from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ResourceCode = Literal[
    "api_requests",
    "platform_model_requests",
    "platform_input_tokens",
    "platform_output_tokens",
]


class BillingPlanEntitlementCreate(BaseModel):
    resource_code: ResourceCode
    unit: str = Field(min_length=1, max_length=40)
    included_units: int = Field(ge=0)
    hard_limit_units: int | None = Field(default=None, ge=0)
    overage_rate_minor_per_unit: Decimal = Field(ge=0, max_digits=24, decimal_places=10)
    overage_behavior: Literal["block"] = "block"

    @field_validator("unit")
    @classmethod
    def trim_unit(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("unit 不能为空")
        return value

    @model_validator(mode="after")
    def validate_limit(self):
        if self.hard_limit_units is not None and self.hard_limit_units < self.included_units:
            raise ValueError("hard_limit_units 不能小于 included_units")
        return self


class BillingPlanCreate(BaseModel):
    code: str = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=100)
    billing_interval: Literal["month", "year"]
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    subscription_price_minor: int = Field(ge=0)
    status: Literal["draft", "active"] = "draft"
    entitlements: list[BillingPlanEntitlementCreate] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("套餐名称不能为空")
        return value

    @model_validator(mode="after")
    def unique_resources(self):
        codes = [item.resource_code for item in self.entitlements]
        if len(codes) != len(set(codes)):
            raise ValueError("同一套餐内资源权益不能重复")
        return self


class BillingPlanEntitlementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    resource_code: str
    unit: str
    included_units: int
    hard_limit_units: int | None
    overage_rate_minor_per_unit: Decimal
    overage_behavior: str


class BillingPlanRead(BaseModel):
    id: UUID
    code: str
    revision: int
    name: str
    billing_interval: str
    currency: str
    subscription_price_minor: int
    status: str
    entitlements: list[BillingPlanEntitlementRead]
    created_at: datetime


class TenantSubscriptionCreate(BaseModel):
    plan_id: UUID
    current_period_start: datetime
    current_period_end: datetime
    status: Literal["active", "trialing"] = "active"

    @model_validator(mode="after")
    def validate_period(self):
        if self.current_period_start.tzinfo is None or self.current_period_end.tzinfo is None:
            raise ValueError("订阅周期时间必须包含时区")
        if self.current_period_end <= self.current_period_start:
            raise ValueError("订阅结束时间必须晚于开始时间")
        return self


class TenantSubscriptionRead(BaseModel):
    id: UUID
    tenant_id: UUID
    plan_id: UUID
    plan_code: str
    plan_name: str
    status: str
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool
    created_at: datetime


class BillingResourceUsageRead(BaseModel):
    resource_code: str
    unit: str
    included_units: int
    hard_limit_units: int | None
    used_units: int
    remaining_units: int
    overage_behavior: str


class BillingEntitlementsRead(BaseModel):
    legacy_unmetered: bool
    subscription: TenantSubscriptionRead | None
    resources: list[BillingResourceUsageRead]
