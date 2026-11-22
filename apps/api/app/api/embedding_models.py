from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.api.platform_admin import record_platform_audit, require_platform_permission
from app.billing.credentials import resolve_platform_api_key
from app.models.knowledge import PlatformEmbeddingModel
from app.schemas.knowledge import (
    PlatformEmbeddingModelAdminRead,
    PlatformEmbeddingModelCreate,
    PlatformEmbeddingModelPublicRead,
    PlatformEmbeddingModelUpdate,
)

router = APIRouter(tags=["平台 Embedding 模型"])


def _admin_read(model: PlatformEmbeddingModel) -> PlatformEmbeddingModelAdminRead:
    return PlatformEmbeddingModelAdminRead(
        id=model.id,
        code=model.code,
        display_name=model.display_name,
        provider_type=model.provider_type,
        upstream_model_name=model.upstream_model_name,
        base_url=model.base_url,
        credential_ref=model.credential_ref,
        dimensions=model.dimensions,
        enabled=model.enabled,
        created_at=model.created_at,
    )


@router.get(
    "/platform/embedding-models",
    response_model=list[PlatformEmbeddingModelPublicRead],
)
def list_public_embedding_models(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[PlatformEmbeddingModelPublicRead]:
    models = db.scalars(
        select(PlatformEmbeddingModel)
        .where(PlatformEmbeddingModel.enabled.is_(True))
        .order_by(PlatformEmbeddingModel.display_name.asc())
    ).all()
    result = []
    for model in models:
        try:
            resolve_platform_api_key(model.credential_ref)
        except HTTPException:
            continue
        result.append(
            PlatformEmbeddingModelPublicRead(
                id=model.id,
                code=model.code,
                display_name=model.display_name,
                model_name=model.upstream_model_name,
                dimensions=model.dimensions,
            )
        )
    return result


@router.get(
    "/admin/platform/embedding-models",
    response_model=list[PlatformEmbeddingModelAdminRead],
)
def list_admin_embedding_models(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[PlatformEmbeddingModelAdminRead]:
    require_platform_permission(principal, "platform.models.read")
    models = db.scalars(
        select(PlatformEmbeddingModel).order_by(PlatformEmbeddingModel.created_at.desc())
    ).all()
    return [_admin_read(model) for model in models]


@router.post(
    "/admin/platform/embedding-models",
    response_model=PlatformEmbeddingModelAdminRead,
    status_code=201,
)
def create_embedding_model(
    payload: PlatformEmbeddingModelCreate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> PlatformEmbeddingModelAdminRead:
    require_platform_permission(principal, "platform.models.manage")
    model = PlatformEmbeddingModel(
        id=uuid4(),
        code=payload.code,
        display_name=payload.display_name,
        provider_type=payload.provider_type,
        upstream_model_name=payload.upstream_model_name,
        base_url=payload.base_url,
        credential_ref=payload.credential_ref,
        dimensions=payload.dimensions,
        enabled=payload.enabled,
    )
    db.add(model)
    record_platform_audit(
        db, actor_user_id=principal.user.id, target_user_id=None,
        action="platform.embedding_model.create", resource_type="embedding_model",
        resource_id=model.code, reason="创建平台 Embedding 模型", request_id=request_id,
        details={
            "provider_type": model.provider_type,
            "dimensions": model.dimensions,
            "enabled": model.enabled,
        },
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "embedding_model_code_exists",
                "message": "平台 Embedding 模型编码已存在",
            },
        ) from None
    db.refresh(model)
    return _admin_read(model)


@router.patch(
    "/admin/platform/embedding-models/{model_id}",
    response_model=PlatformEmbeddingModelAdminRead,
)
def update_embedding_model(
    model_id: UUID,
    payload: PlatformEmbeddingModelUpdate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> PlatformEmbeddingModelAdminRead:
    require_platform_permission(principal, "platform.models.manage")
    model = db.get(PlatformEmbeddingModel, model_id)
    if model is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "embedding_model_not_found", "message": "平台 Embedding 模型不存在"},
        )
    updates = payload.model_dump(exclude_unset=True)
    if any(value is None for value in updates.values()):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "null_not_allowed",
                "message": "平台 Embedding 模型字段不允许设置为 null",
            },
        )
    for field, value in updates.items():
        setattr(model, field, value)
    record_platform_audit(
        db, actor_user_id=principal.user.id, target_user_id=None,
        action="platform.embedding_model.update", resource_type="embedding_model",
        resource_id=str(model.id), reason="修改平台 Embedding 模型", request_id=request_id,
        details={"changed_fields": sorted(updates)},
    )
    db.commit()
    db.refresh(model)
    return _admin_read(model)
