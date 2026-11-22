from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "developer", "user", "assistant"]
    content: str = Field(max_length=200000)
    name: str | None = Field(default=None, max_length=64)


class SenderProfile(BaseModel):
    """Transient third-party contact metadata for one chat completion."""

    model_config = ConfigDict(extra="forbid")

    wxid: str = Field(min_length=1, max_length=255)
    nickname: str | None = Field(default=None, max_length=160)
    remark: str | None = Field(default=None, max_length=160)
    avatar_url: str | None = Field(default=None, max_length=2048)
    labels: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("wxid", "nickname", "remark", mode="before")
    @classmethod
    def trim_profile_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            label = item.strip()
            if not label:
                continue
            if len(label) > 80:
                raise ValueError("联系人标签长度不能超过 80 个字符")
            if label not in normalized:
                normalized.append(label)
        return normalized


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=80)
    messages: list[ChatMessage] = Field(min_length=1, max_length=1000)
    user: str | None = Field(default=None, min_length=1, max_length=255)
    sender_profile: SenderProfile | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1, le=65536)
    stream: bool = False
    stop: str | list[str] | None = None
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)

    @model_validator(mode="after")
    def validate_sender_identity(self) -> "ChatCompletionRequest":
        if self.user is not None:
            self.user = self.user.strip()
            if not self.user:
                raise ValueError("user 不能为空")
        if self.sender_profile is not None:
            if self.user is None:
                self.user = self.sender_profile.wxid
            elif self.user != self.sender_profile.wxid:
                raise ValueError("user 必须与 sender_profile.wxid 一致")
        return self

    @field_validator("model", mode="before")
    @classmethod
    def trim_model(cls, value: str) -> str:
        if isinstance(value, str):
            return value.strip()
        return value
