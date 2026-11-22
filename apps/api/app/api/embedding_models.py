from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.api.platform_admin import record_platform_audit, require_platform_permission
from app.billing.credentials import resolve_platform_api_key
from app.knowledge.embeddings import EmbeddingConfig, embed_texts
from app.knowledge.network import validate_public_service_url
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
        verified_at=model.verified_at,
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
        .where(
            PlatformEmbeddingModel.enabled.is_(True),
            PlatformEmbeddingModel.verified_at.is_not(None),
        )
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
    if payload.enabled:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "embedding_model_test_required",
                "message": "请先创建模型、测试连接并确认返回 1536 维向量，再启用模型",
            },
        )
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
    connection_fields = {
        "provider_type", "upstream_model_name", "base_url", "credential_ref", "dimensions"
    }
    connection_changed = any(
        getattr(model, field) != updates[field]
        for field in connection_fields.intersection(updates)
    )
    if connection_changed and updates.get("enabled") is True:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "embedding_model_retest_required",
                "message": "修改了服务商、模型、地址或凭据后，必须重新测试连接才能启用",
            },
        )
    if updates.get("enabled") is True and model.verified_at is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "embedding_model_test_required",
                "message": "请先测试连接并确认返回 1536 维向量，再启用模型",
            },
        )
    if connection_changed:
        model.verified_at = None
        model.enabled = False
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



def _probe_embedding_model(model: PlatformEmbeddingModel) -> dict:
    api_key = resolve_platform_api_key(model.credential_ref)
    validate_public_service_url(model.base_url)
    result = embed_texts(
        EmbeddingConfig(
            base_url=model.base_url,
            api_key=api_key,
            provider_type=model.provider_type,
            model_name=model.upstream_model_name,
            dimensions=model.dimensions,
        ),
        ["SuperNova Embedding connectivity and 1536-dimensional vector validation."],
    )
    if len(result.vectors) != 1 or len(result.vectors[0]) != 1536:
        raise RuntimeError("embedding_provider_dimension_mismatch")
    return {
        "success": True,
        "model_id": str(model.id),
        "model_name": model.upstream_model_name,
        "dimensions": len(result.vectors[0]),
        "expected_dimensions": 1536,
        "input_tokens": result.input_tokens if result.usage_known else None,
    }


@router.post(
    "/admin/platform/embedding-models/{model_id}/test",
)
def test_embedding_model_connection(
    model_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    require_platform_permission(principal, "platform.models.manage")
    model = db.get(PlatformEmbeddingModel, model_id)
    if model is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "embedding_model_not_found", "message": "平台 Embedding 模型不存在"},
        )
    try:
        result = _probe_embedding_model(model)
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail={"code": "embedding_provider_timeout", "message": "Embedding 服务连接超时"},
        ) from None
    except httpx.RequestError:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "embedding_provider_unreachable",
                "message": "无法连接 Embedding 服务，请检查 Base URL 和网络",
            },
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"code": "embedding_provider_url_invalid", "message": "Embedding 服务地址不符合安全要求"},
        ) from None
    except RuntimeError as exc:
        reason = str(exc)
        messages = {
            "embedding_provider_dimension_mismatch": "服务返回的向量维度不是 1536，不能用于当前知识库索引",
            "embedding_provider_response_count_mismatch": "Embedding 服务返回的向量数量不正确",
            "embedding_provider_invalid_json": "Embedding 服务返回了无效 JSON",
            "embedding_provider_invalid_vector": "Embedding 服务返回了无效向量",
            "embedding_provider_http_401": "Embedding API Key 无效或已过期",
            "embedding_provider_http_403": "当前 API Key 无权调用该 Embedding 模型",
            "embedding_provider_http_404": "Embedding 接口或模型不存在，请检查 Base URL 和模型 ID",
            "embedding_provider_http_429": "Embedding 服务请求过于频繁，请稍后重试",
        }
        raise HTTPException(
            status_code=502,
            detail={
                "code": reason if reason in messages else "embedding_provider_test_failed",
                "message": messages.get(reason, "Embedding 服务测试失败，请检查模型名称和服务商配置"),
            },
        ) from None

    model.verified_at = datetime.now(UTC)
    record_platform_audit(
        db,
        actor_user_id=principal.user.id,
        target_user_id=None,
        action="platform.embedding_model.test",
        resource_type="embedding_model",
        resource_id=str(model.id),
        reason="测试平台 Embedding 模型连接及向量维度",
        request_id=request_id,
        details={"dimensions": result["dimensions"], "success": True},
    )
    db.commit()
    return result
