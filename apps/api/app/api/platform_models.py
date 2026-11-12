from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.api.platform_admin import require_platform_admin
from app.billing.credentials import resolve_platform_api_key
from app.models.billing import PlatformModel, PlatformModelPrice, TenantBillingPolicy
from app.schemas.platform_models import (
    PlatformModelAdminRead,
    PlatformModelCreate,
    PlatformModelPriceCreate,
    PlatformModelPriceRead,
    PlatformModelPublicRead,
    PlatformModelUpdate,
)

router = APIRouter(tags=["平台模型池"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "platform_model_not_found", "message": "平台模型不存在"},
    )


def _admin_read(model: PlatformModel) -> PlatformModelAdminRead:
    return PlatformModelAdminRead(
        id=model.id,
        code=model.code,
        display_name=model.display_name,
        provider_type=model.provider_type,
        upstream_model_name=model.upstream_model_name,
        base_url=model.base_url,
        credential_ref=model.credential_ref,
        enabled=model.enabled,
        created_at=model.created_at,
    )


@router.get("/platform/models", response_model=list[PlatformModelPublicRead])
def list_public_platform_models(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[PlatformModelPublicRead]:
    now = datetime.now(UTC)
    policy = db.get(TenantBillingPolicy, principal.tenant.id)
    currency = policy.currency if policy is not None else "CNY"
    rows = db.execute(
        select(PlatformModel, PlatformModelPrice)
        .join(PlatformModelPrice, PlatformModelPrice.platform_model_id == PlatformModel.id)
        .where(
            PlatformModel.enabled.is_(True),
            PlatformModel.provider_type != "anthropic",
            PlatformModelPrice.currency == currency,
            PlatformModelPrice.effective_from <= now,
            or_(
                PlatformModelPrice.effective_until.is_(None),
                PlatformModelPrice.effective_until > now,
            ),
        )
        .order_by(PlatformModel.display_name.asc(), PlatformModelPrice.revision.desc())
    ).all()
    result: list[PlatformModelPublicRead] = []
    seen: set[UUID] = set()
    for model, price in rows:
        if model.id in seen:
            continue
        try:
            resolve_platform_api_key(model.credential_ref)
        except HTTPException:
            continue
        seen.add(model.id)
        result.append(
            PlatformModelPublicRead(
                id=model.id,
                code=model.code,
                display_name=model.display_name,
                provider_type=model.provider_type,
                model_name=model.upstream_model_name,
                currency=price.currency,
                input_per_million_minor=price.input_per_million_minor,
                output_per_million_minor=price.output_per_million_minor,
                price_revision=price.revision,
            )
        )
    return result


@router.get("/admin/platform/models", response_model=list[PlatformModelAdminRead])
def list_admin_platform_models(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[PlatformModelAdminRead]:
    require_platform_admin(principal)
    models = db.scalars(
        select(PlatformModel).order_by(PlatformModel.created_at.desc())
    ).all()
    return [_admin_read(model) for model in models]


@router.post(
    "/admin/platform/models",
    response_model=PlatformModelAdminRead,
    status_code=status.HTTP_201_CREATED,
)
def create_platform_model(
    payload: PlatformModelCreate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> PlatformModelAdminRead:
    require_platform_admin(principal)
    model = PlatformModel(
        id=uuid4(),
        code=payload.code,
        display_name=payload.display_name,
        provider_type=payload.provider_type,
        upstream_model_name=payload.upstream_model_name,
        base_url=payload.base_url,
        credential_ref=payload.credential_ref,
        enabled=payload.enabled,
    )
    db.add(model)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "platform_model_code_exists", "message": "平台模型编码已存在"},
        ) from None
    db.refresh(model)
    return _admin_read(model)


@router.patch(
    "/admin/platform/models/{model_id}",
    response_model=PlatformModelAdminRead,
)
def update_platform_model(
    model_id: UUID,
    payload: PlatformModelUpdate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> PlatformModelAdminRead:
    require_platform_admin(principal)
    model = db.get(PlatformModel, model_id)
    if model is None:
        raise _not_found()
    updates = payload.model_dump(exclude_unset=True)
    if any(value is None for value in updates.values()):
        raise HTTPException(
            status_code=422,
            detail={"code": "null_not_allowed", "message": "平台模型配置不允许设置为 null"},
        )
    for field, value in updates.items():
        setattr(model, field, value)
    db.commit()
    db.refresh(model)
    return _admin_read(model)


@router.get(
    "/admin/platform/models/{model_id}/prices",
    response_model=list[PlatformModelPriceRead],
)
def list_platform_model_prices(
    model_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[PlatformModelPriceRead]:
    require_platform_admin(principal)
    if db.get(PlatformModel, model_id) is None:
        raise _not_found()
    prices = db.scalars(
        select(PlatformModelPrice)
        .where(PlatformModelPrice.platform_model_id == model_id)
        .order_by(PlatformModelPrice.revision.desc())
    ).all()
    return [PlatformModelPriceRead.model_validate(price) for price in prices]


@router.post(
    "/admin/platform/models/{model_id}/prices",
    response_model=PlatformModelPriceRead,
    status_code=status.HTTP_201_CREATED,
)
def create_platform_model_price(
    model_id: UUID,
    payload: PlatformModelPriceCreate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> PlatformModelPriceRead:
    require_platform_admin(principal)
    if db.get(PlatformModel, model_id) is None:
        raise _not_found()

    new_start = payload.effective_from
    new_end = payload.effective_until
    # Close the prior open-ended version at the new version's start. Price
    # values remain immutable; only the previous effective interval is closed.
    open_versions = db.scalars(
        select(PlatformModelPrice)
        .where(
            PlatformModelPrice.platform_model_id == model_id,
            PlatformModelPrice.currency == payload.currency,
            PlatformModelPrice.effective_until.is_(None),
            PlatformModelPrice.effective_from < new_start,
        )
        .with_for_update()
    ).all()
    for previous in open_versions:
        previous.effective_until = new_start
    if open_versions:
        db.flush()

    overlap_query = select(PlatformModelPrice.id).where(
        PlatformModelPrice.platform_model_id == model_id,
        PlatformModelPrice.currency == payload.currency,
        or_(
            PlatformModelPrice.effective_until.is_(None),
            PlatformModelPrice.effective_until > new_start,
        ),
    )
    if new_end is not None:
        overlap_query = overlap_query.where(PlatformModelPrice.effective_from < new_end)
    overlaps = db.scalar(overlap_query.limit(1))
    if overlaps is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "platform_model_price_period_overlap",
                "message": "同一币种的模型价格生效周期不能重叠；请保留历史版本并设置清晰的生效时间",
            },
        )

    revision = int(
        db.scalar(
            select(func.coalesce(func.max(PlatformModelPrice.revision), 0)).where(
                PlatformModelPrice.platform_model_id == model_id
            )
        )
        or 0
    ) + 1
    price = PlatformModelPrice(
        id=uuid4(),
        platform_model_id=model_id,
        revision=revision,
        currency=payload.currency,
        input_per_million_minor=payload.input_per_million_minor,
        output_per_million_minor=payload.output_per_million_minor,
        effective_from=payload.effective_from,
        effective_until=payload.effective_until,
    )
    db.add(price)
    db.commit()
    db.refresh(price)
    return PlatformModelPriceRead.model_validate(price)
