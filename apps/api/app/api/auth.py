from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_current_principal
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    hash_password,
    hash_secret,
    new_refresh_token,
    verify_password,
)
from app.db.session import get_db
from app.models.identity import RefreshSession, Tenant, TenantMembership, User
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    TenantRead,
    UserRead,
)

router = APIRouter(prefix="/auth", tags=["身份认证"])


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _new_refresh_session(
    user_id: UUID,
    tenant_id: UUID,
    family_id: UUID | None = None,
) -> tuple[str, RefreshSession]:
    settings = get_settings()
    token = new_refresh_token()
    now = datetime.now(UTC)
    record = RefreshSession(
        id=uuid4(),
        family_id=family_id or uuid4(),
        user_id=user_id,
        tenant_id=tenant_id,
        token_hash=hash_secret(token),
        expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
    )
    return token, record


def _auth_response(user: User, tenant: Tenant, membership: TenantMembership, refresh: str) -> AuthResponse:
    settings = get_settings()
    return AuthResponse(
        access_token=create_access_token(user.id, tenant.id),
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_minutes * 60,
        user=UserRead.model_validate(user),
        tenant=TenantRead(id=tenant.id, name=tenant.name, role=membership.role),
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_credentials", "message": "登录凭据无效或已过期"},
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> AuthResponse:
    email = str(payload.email).lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "email_already_registered", "message": "该邮箱已注册"},
        )

    display_name = payload.display_name.strip()
    tenant_name = (payload.workspace_name or f"{display_name} 的工作空间").strip()
    user = User(
        id=uuid4(),
        email=email,
        display_name=display_name,
        password_hash=hash_password(payload.password),
        status="active",
    )
    tenant = Tenant(id=uuid4(), name=tenant_name, status="active")
    membership = TenantMembership(
        id=uuid4(),
        tenant_id=tenant.id,
        user_id=user.id,
        role="owner",
        status="active",
    )
    refresh_token, refresh_session = _new_refresh_session(user.id, tenant.id)

    db.add_all([user, tenant, membership, refresh_session])
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "email_already_registered", "message": "该邮箱已注册"},
        ) from None

    db.refresh(user)
    db.refresh(tenant)
    db.refresh(membership)
    return _auth_response(user, tenant, membership, refresh_token)


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    email = str(payload.email).lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise _unauthorized()
    if user.status != "active":
        raise _unauthorized()

    membership = db.scalar(
        select(TenantMembership)
        .where(TenantMembership.user_id == user.id, TenantMembership.status == "active")
        .order_by(TenantMembership.created_at.asc())
    )
    if membership is None:
        raise _unauthorized()
    tenant = db.scalar(
        select(Tenant).where(Tenant.id == membership.tenant_id, Tenant.status == "active")
    )
    if tenant is None:
        raise _unauthorized()

    refresh_token, refresh_session = _new_refresh_session(user.id, tenant.id)
    db.add(refresh_session)
    db.commit()
    return _auth_response(user, tenant, membership, refresh_token)


@router.post("/refresh", response_model=AuthResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> AuthResponse:
    token_hash = hash_secret(payload.refresh_token)
    record = db.scalar(
        select(RefreshSession)
        .where(RefreshSession.token_hash == token_hash)
        .with_for_update()
    )
    if record is None:
        raise _unauthorized()

    now = datetime.now(UTC)
    if record.revoked_at is not None:
        db.execute(
            update(RefreshSession)
            .where(
                RefreshSession.family_id == record.family_id,
                RefreshSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        db.commit()
        raise _unauthorized()

    if _aware(record.expires_at) <= now:
        record.revoked_at = now
        db.commit()
        raise _unauthorized()

    user = db.scalar(select(User).where(User.id == record.user_id, User.status == "active"))
    tenant = db.scalar(
        select(Tenant).where(Tenant.id == record.tenant_id, Tenant.status == "active")
    )
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.user_id == record.user_id,
            TenantMembership.tenant_id == record.tenant_id,
            TenantMembership.status == "active",
        )
    )
    if user is None or tenant is None or membership is None:
        record.revoked_at = now
        db.commit()
        raise _unauthorized()

    new_token, new_record = _new_refresh_session(
        record.user_id,
        record.tenant_id,
        family_id=record.family_id,
    )
    record.revoked_at = now
    db.add(new_record)
    db.commit()
    return _auth_response(user, tenant, membership, new_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: LogoutRequest, response: Response, db: Session = Depends(get_db)) -> Response:
    if payload.refresh_token:
        record = db.scalar(
            select(RefreshSession).where(
                RefreshSession.token_hash == hash_secret(payload.refresh_token)
            )
        )
        if record is not None and record.revoked_at is None:
            record.revoked_at = datetime.now(UTC)
            db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeResponse)
def me(principal: Principal = Depends(get_current_principal)) -> MeResponse:
    return MeResponse(
        user=UserRead.model_validate(principal.user),
        tenant=TenantRead(
            id=principal.tenant.id,
            name=principal.tenant.name,
            role=principal.membership.role,
        ),
    )
