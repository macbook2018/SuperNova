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
        if not cleaned:
            if value is not None and value == "":
                return None
            if value is not None:
                raise ValueError("该字段不能只包含空白字符")
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
    avatar_asset_id: UUID | None = None


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
        if not cleaned and value is not None:
            raise ValueError("目标发送者标识不能为空")
        return cleaned


class ChatImportFileRead(BaseModel):
    id: UUID
    original_filename: str
    extension: str
    mime_type: str
    file_size_bytes: int
    sha256: str
    status: str
    parsed_message_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class ChatImportJobDetail(ChatImportJobRead):
    files: list[ChatImportFileRead]


class ChatMediaAssetRead(BaseModel):
    id: UUID
    asset_type: str
    role: str
    status: str
    original_name: str | None
    mime_type: str | None
    file_size_bytes: int
    width: int | None
    height: int | None
    content_url: str | None


class ChatMessageRead(BaseModel):
    id: UUID
    conversation_id: UUID
    sender_ref: str
    sender_name: str
    receiver_ref: str | None
    receiver_name: str | None
    content: str
    message_type: str
    timestamp: datetime
    identity_role: str
    metadata: dict
    media_assets: list[ChatMediaAssetRead] = Field(default_factory=list)


class ChatMediaExtractionJobRead(BaseModel):
    id: UUID
    source_id: UUID
    import_file_id: UUID
    status: str
    progress: int
    total_assets: int
    extracted_assets: int
    missing_assets: int
    error_count: int
    error_summary: str | None
    attempts: int
    created_at: datetime
    updated_at: datetime


class ChatMediaExtractionAccepted(BaseModel):
    jobs: list[ChatMediaExtractionJobRead]
    accepted_files: int
    reused_jobs: int


