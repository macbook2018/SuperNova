from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_IMAGE_DATA_URL_CHARS = ((_MAX_IMAGE_BYTES + 2) // 3) * 4 + 64
_IMAGE_DATA_URL = re.compile(
    r"^data:(image/(?:jpeg|png|gif|webp));base64,([A-Za-z0-9+/]+={0,2})$"
)


def _clean_text(value: str, *, max_utf16_units: int, field_name: str) -> str:
    cleaned = "".join(char for char in value if unicodedata.category(char) != "Cc").strip()
    utf16_units = len(cleaned.encode("utf-16-le")) // 2
    if utf16_units > max_utf16_units:
        raise ValueError(f"{field_name}不能超过 {max_utf16_units} 个 UTF-16 单元")
    return cleaned


class ImageURL(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=_MAX_IMAGE_DATA_URL_CHARS)

    @field_validator("url")
    @classmethod
    def validate_image_data_url(cls, value: str) -> str:
        match = _IMAGE_DATA_URL.fullmatch(value)
        if match is None:
            raise ValueError(
                "图片必须使用 image/jpeg、image/png、image/gif 或 image/webp 的 Base64 Data URL"
            )
        encoded = match.group(2)
        if len(encoded) > ((_MAX_IMAGE_BYTES + 2) // 3) * 4:
            raise ValueError("图片文件不能超过 20 MiB")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("图片 Base64 数据无效") from None
        if not decoded or len(decoded) > _MAX_IMAGE_BYTES:
            raise ValueError("图片文件必须大于 0 且不能超过 20 MiB")
        return value


class TextContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    text: str = Field(max_length=200000)


class ImageContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["image_url"]
    image_url: ImageURL


ContentPart = Annotated[TextContentPart | ImageContentPart, Field(discriminator="type")]


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "developer", "user", "assistant"]
    content: str | list[ContentPart] = Field(max_length=200000)
    name: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_content_parts(self) -> "ChatMessage":
        if isinstance(self.content, list):
            if not self.content:
                raise ValueError("消息 content 数组不能为空")
            image_count = sum(isinstance(part, ImageContentPart) for part in self.content)
            if image_count > 1:
                raise ValueError("每条消息最多允许一个图片媒体段")
        return self


class SenderProfile(BaseModel):
    """Verified WeChat sender metadata supplied for the current message only."""

    model_config = ConfigDict(extra="forbid")

    wxid: str = Field(min_length=1, max_length=255)
    nickname: str = ""
    remark: str = ""
    labels: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("wxid", mode="before")
    @classmethod
    def clean_wxid(cls, value: str) -> str:
        if isinstance(value, str):
            cleaned = _clean_text(value, max_utf16_units=255, field_name="sender_profile.wxid")
            if not cleaned:
                raise ValueError("sender_profile.wxid 不能为空")
            return cleaned
        return value

    @field_validator("nickname", "remark", mode="before")
    @classmethod
    def clean_profile_text(cls, value: str) -> str:
        if isinstance(value, str):
            field_name = "昵称" if cls.__name__ == "SenderProfile" else "联系人资料"
            return _clean_text(value, max_utf16_units=256, field_name=field_name)
        return value

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            label = _clean_text(item, max_utf16_units=128, field_name="联系人标签")
            if label and label not in normalized:
                normalized.append(label)
        return normalized


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=80)
    messages: list[ChatMessage] = Field(min_length=1, max_length=1000)
    # For WeChat integrations, user is the conversation/session ID, not the sender wxid.
    user: str | None = Field(default=None, min_length=1, max_length=512)
    # The logged-in WeChat account identity is independent of the conversation and speaker.
    account_wxid: str | None = Field(default=None, min_length=1, max_length=255)
    sender_profile: SenderProfile | None = None
    # Present only for some group voice-transcription requests.
    speaker: str | None = Field(default=None, min_length=1, max_length=255)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1, le=65536)
    stream: bool = False
    stop: str | list[str] | None = None
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)

    @field_validator("user", "account_wxid", "speaker", mode="before")
    @classmethod
    def normalize_identity_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = _clean_text(value, max_utf16_units=512, field_name="身份字段")
            return normalized or None
        return value

    @model_validator(mode="after")
    def validate_session_identity(self) -> "ChatCompletionRequest":
        if (
            self.user is None
            and (
                self.account_wxid is not None
                or self.sender_profile is not None
                or self.speaker is not None
            )
        ):
            raise ValueError("提供微信身份或发言人资料时，必须同时提供 user 会话标识")
        return self

    @field_validator("model", mode="before")
    @classmethod
    def trim_model(cls, value: str) -> str:
        if isinstance(value, str):
            return value.strip()
        return value
