from fastapi import HTTPException, status

from app.api.deps import Principal
from app.core.config import get_settings


def require_platform_admin(principal: Principal) -> None:
    allowed = get_settings().platform_admin_email_set
    if not allowed or principal.user.email.strip().lower() not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "platform_admin_required",
                "message": "只有平台管理员可以执行此操作",
            },
        )
