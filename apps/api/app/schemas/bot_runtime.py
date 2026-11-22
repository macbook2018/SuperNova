from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.gateway import ChatMessage

DesiredState = Literal["running", "stopped", "deleting"]
ActualState = Literal[
    "pending",
    "provisioning",
    "starting",
    "running",
    "stopping",
    "stopped",
    "unhealthy",
    "failed",
    "deleting",
]
RuntimeCommandName = Literal["start", "stop", "restart", "delete", "update_config"]


def _trim_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("此字段不能为空")
    return value


class BotInstanceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    persona_id: UUID

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _trim_required(value)


class BotInstanceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    persona_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return _trim_required(value) if value is not None else None


class BotInstanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    persona_id: UUID
    created_by_user_id: UUID
    name: str
    runtime_version: str
    desired_state: DesiredState
    actual_state: ActualState
    config_version: int
    runtime_provider: str
    runtime_resource_id: str | None
    last_heartbeat_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime


class BotRuntimeCommandRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    bot_instance_id: UUID
    command: RuntimeCommandName
    idempotency_key: str
    status: Literal["queued", "leased", "succeeded", "failed", "cancelled"]
    attempts: int
    max_attempts: int
    available_at: datetime
    lease_expires_at: datetime | None
    worker_id: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class LifecycleCommandResponse(BaseModel):
    bot_instance_id: UUID
    desired_state: DesiredState
    command: BotRuntimeCommandRead | None
    already_satisfied: bool = False


class BotRuntimeEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    schema_version: int
    channel_account_id: UUID | None
    source_account_id: UUID | None
    platform: str
    correlation_id: UUID
    occurred_at: datetime
    payload: dict[str, Any] = Field(validation_alias="payload_json")


class BotRuntimeLogsRead(BaseModel):
    items: list[BotRuntimeEventRead]
    next_cursor: UUID | None


class RuntimeHeartbeatRequest(BaseModel):
    config_version: int = Field(ge=1)
    runtime_version: str = Field(min_length=1, max_length=100)
    state: Literal["running", "unhealthy"] = "running"
    health_code: str | None = Field(default=None, max_length=100)


class RuntimeHeartbeatResponse(BaseModel):
    accepted: bool = True
    server_time: datetime
    config_version: int


_ALLOWED_EVENT_PAYLOAD_KEYS = {
    "level",
    "component",
    "error_code",
    "retryable",
    "attempt",
    "duration_ms",
    "state",
    "resource_id",
    "code",
    "reason_code",
    "message_id",
    "retry_in_seconds",
}


class RuntimeEventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: str = Field(min_length=1, max_length=64)
    schema_version: int = Field(default=1, ge=1)
    tenant_id: UUID
    bot_instance_id: UUID
    channel_account_id: UUID | None = None
    source_account_id: UUID | None = None
    platform: str = Field(default="system", min_length=1, max_length=32)
    correlation_id: UUID
    occurred_at: datetime
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def validate_safe_payload(
        cls, value: dict[str, str | int | float | bool | None]
    ) -> dict[str, str | int | float | bool | None]:
        if len(value) > 24:
            raise ValueError("运行事件 payload 字段过多")
        unknown = set(value) - _ALLOWED_EVENT_PAYLOAD_KEYS
        if unknown:
            raise ValueError("运行事件只能包含允许的非聊天元数据字段")
        for item in value.values():
            if isinstance(item, str) and len(item) > 256:
                raise ValueError("运行事件元数据字符串不能超过 256 个字符")
        return value


class RuntimeEventResponse(BaseModel):
    accepted: bool = True
    duplicate: bool = False


class RuntimeChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, max_length=80)
    messages: list[ChatMessage] = Field(min_length=1, max_length=1000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1, le=65536)
    stream: bool = False
    stop: str | list[str] | None = None
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
