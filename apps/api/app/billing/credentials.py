import json

from fastapi import HTTPException

from app.core.config import get_settings


def resolve_platform_api_key(credential_ref: str | None) -> str:
    if not credential_ref:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "platform_credentials_missing",
                "message": "平台模型凭据未配置，请联系平台管理员",
            },
        )
    raw = get_settings().platform_credentials_json or "{}"
    try:
        credentials = json.loads(raw)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "platform_credentials_unavailable",
                "message": "平台模型凭据配置格式错误",
            },
        ) from None
    if not isinstance(credentials, dict):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "platform_credentials_unavailable",
                "message": "平台模型凭据配置格式错误",
            },
        )
    secret = credentials.get(credential_ref)
    if not isinstance(secret, str) or not secret.strip():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "platform_credentials_missing",
                "message": "平台模型凭据未配置，请联系平台管理员",
            },
        )
    return secret.strip()
