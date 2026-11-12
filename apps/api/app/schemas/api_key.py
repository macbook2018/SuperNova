from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        if isinstance(obj, dict) and isinstance(obj.get("name"), str):
            obj = {**obj, "name": obj["name"].strip()}
        return super().model_validate(obj, *args, **kwargs)


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    key_prefix: str
    status: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(BaseModel):
    api_key: ApiKeyRead
    secret: str
    warning: str = "密钥明文只展示本次，请立即复制并妥善保存。"
