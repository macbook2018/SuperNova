from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_current_principal
from app.core.security import hash_secret, new_api_key
from app.db.session import get_db
from app.models.identity import ApiKey
from app.schemas.api_key import ApiKeyCreateRequest, ApiKeyCreated, ApiKeyRead

router = APIRouter(prefix="/api-keys", tags=["用户级 API 密钥"])


def _create_record(
    principal: Principal,
    name: str,
    expires_at: datetime | None,
) -> tuple[ApiKey, str]:
    secret = new_api_key()
    record = ApiKey(
        id=uuid4(),
        tenant_id=principal.tenant.id,
        user_id=principal.user.id,
        name=name,
        key_prefix=secret[:16],
        key_hash=hash_secret(secret),
        status="active",
        expires_at=expires_at,
    )
    return record, secret


def _created_response(record: ApiKey, secret: str) -> ApiKeyCreated:
    return ApiKeyCreated(
        api_key=ApiKeyRead.model_validate(record),
        secret=secret,
    )


def _owned_key(db: Session, principal: Principal, key_id: UUID) -> ApiKey | None:
    return db.scalar(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.user_id == principal.user.id,
            ApiKey.tenant_id == principal.tenant.id,
        )
    )


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_api_key(
    payload: ApiKeyCreateRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ApiKeyCreated:
    name = payload.name.strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_key_name", "message": "密钥名称不能为空"},
        )
    expires_at = (
        datetime.now(UTC) + timedelta(days=payload.expires_in_days)
        if payload.expires_in_days
        else None
    )
    record, secret = _create_record(principal, name, expires_at)
    db.add(record)
    db.commit()
    db.refresh(record)
    return _created_response(record, secret)


@router.get("", response_model=list[ApiKeyRead])
def list_api_keys(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[ApiKeyRead]:
    records = db.scalars(
        select(ApiKey)
        .where(
            ApiKey.user_id == principal.user.id,
            ApiKey.tenant_id == principal.tenant.id,
        )
        .order_by(ApiKey.created_at.desc())
    ).all()
    now = datetime.now(UTC)
    result = []
    for record in records:
        item = ApiKeyRead.model_validate(record)
        if record.status == "active" and record.expires_at is not None:
            expires_at = record.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                item.status = "expired"
        result.append(item)
    return result


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> Response:
    record = _owned_key(db, principal, key_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "api_key_not_found", "message": "API 密钥不存在"},
        )
    if record.status == "active":
        record.status = "revoked"
        record.revoked_at = datetime.now(UTC)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{key_id}/rotate", response_model=ApiKeyCreated)
def rotate_api_key(
    key_id: UUID,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ApiKeyCreated:
    record = _owned_key(db, principal, key_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "api_key_not_found", "message": "API 密钥不存在"},
        )
    now = datetime.now(UTC)
    if record.status != "active" or (
        record.expires_at is not None
        and (record.expires_at.replace(tzinfo=UTC) if record.expires_at.tzinfo is None else record.expires_at) <= now
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "api_key_not_active", "message": "只有有效密钥可以轮换"},
        )

    new_record, secret = _create_record(principal, record.name, record.expires_at)
    record.status = "revoked"
    record.revoked_at = now
    db.add(new_record)
    db.commit()
    db.refresh(new_record)
    return _created_response(new_record, secret)
