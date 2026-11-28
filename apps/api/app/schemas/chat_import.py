from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ChatSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    platform: Literal["wechat"] = "wechat"
    export_tool: str | None = Field(default=None, max_length=120)

    @field_validator("name", "export_tool")
    @classmethod
    def trim_values(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            if value == "":
                return None
            raise ValueError("该字段不能只包含空白字符")
        return cleaned


class ChatSourceRead(BaseModel):
    id: UUID
    source_account_id: UUID
    platform: str
    name: str
    export_tool: str | None
    target_sender_ref: str | None
    target_sender_name: str | None
    identity_confirmed_at: datetime | None
    status: str
    created_at: datetime
    updated_at: datetime


class ChatImportJobRead(BaseModel):
    id: UUID
    source_account_id: UUID
    status: str
    progress: int
    total_files: int
    processed_files: int
    parsed_text_messages: int
    ignored_non_text_messages: int
    unique_senders: int
    error_count: int
    error_summary: str | None
    attempts: int
    created_at: datetime
    updated_at: datetime


class ChatImportAccepted(BaseModel):
    job: ChatImportJobRead
    accepted_files: int
    skipped_duplicate_files: int


class ChatImportSenderPreview(BaseModel):
    sender_ref: str
    sender_name: str
    message_count: int


class ChatImportPreview(BaseModel):
    source_account_id: UUID
    identity_confirmed: bool
    target_sender_ref: str | None
    senders: list[ChatImportSenderPreview]


class ChatIdentityConfirm(BaseModel):
    target_sender_ref: str = Field(min_length=1, max_length=255)
    target_sender_name: str | None = Field(default=None, max_length=160)

    @field_validator("target_sender_ref", "target_sender_name")
    @classmethod
    def trim_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("目标发送者标识不能为空")
        return cleaned
