from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ChatSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    export_tool: str | None = Field(default=None, max_length=120)

    @field_validator("name", "export_tool")
    @classmethod
    def trim_values(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned and value == "":
            return None
        return cleaned


class ChatSourceRead(BaseModel):
    id: UUID
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
    source_id: UUID
    status: str
    progress: int
    total_files: int
    processed_files: int
    parsed_messages: int
    imported_messages: int
    duplicate_messages: int
    error_count: int
    error_summary: str | None
    attempts: int
    created_at: datetime
    updated_at: datetime


class ChatImportAccepted(BaseModel):
    job: ChatImportJobRead
    accepted_files: int
    skipped_duplicate_files: int


class ChatImportParticipantPreview(BaseModel):
    sender_ref: str
    sender_name: str
    message_count: int
    conversation_count: int
    identity_role: str


class ChatConversationPreview(BaseModel):
    id: UUID
    external_conversation_id: str
    title: str | None
    conversation_type: str
    message_count: int
    first_message_at: datetime | None
    last_message_at: datetime | None


class ChatImportPreview(BaseModel):
    source_id: UUID
    identity_confirmed: bool
    target_sender_ref: str | None
    participants: list[ChatImportParticipantPreview]
    conversations: list[ChatConversationPreview]


class ChatIdentityConfirm(BaseModel):
    target_sender_ref: str = Field(min_length=1, max_length=255)
    target_sender_name: str | None = Field(default=None, max_length=160)

    @field_validator("target_sender_ref", "target_sender_name")
    @classmethod
    def trim_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None
