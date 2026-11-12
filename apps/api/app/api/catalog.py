from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, insert, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, DatabaseSession, Principal
from app.core.secret_encryption import encrypt_provider_api_key
from app.models.billing import PlatformModel, PlatformModelPrice, TenantBillingPolicy
from app.models.catalog import (
    KnowledgeBase,
    ModelProvider,
    Persona,
    PersonaKnowledgeBase,
)
from app.schemas.catalog import (
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
    ModelProviderCreate,
    ModelProviderRead,
    ModelProviderUpdate,
    PersonaCreate,
    PersonaRead,
    PersonaUpdate,
)

router = APIRouter(tags=["人格、模型供应商与知识库"])


def _not_found(resource: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": f"{resource}_not_found", "message": "资源不存在"},
    )


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message},
    )


def _reject_null_updates(updates: dict, nullable_fields: set[str] | None = None) -> None:
    allowed_nulls = nullable_fields or set()
    invalid = [
        field
        for field, value in updates.items()
        if value is None and field not in allowed_nulls
    ]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "null_not_allowed",
                "message": "以下字段不允许设置为 null：" + ", ".join(sorted(invalid)),
            },
        )


def _provider_read(provider: ModelProvider) -> ModelProviderRead:
    hint = f"••••{provider.api_key_last4}" if provider.api_key_last4 else None
    return ModelProviderRead(
        id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        base_url=provider.base_url,
        enabled=provider.enabled,
        has_api_key=bool(provider.api_key_ciphertext),
        api_key_hint=hint,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _get_provider(db: Session, principal: Principal, provider_id: UUID) -> ModelProvider:
    provider = db.scalar(
        select(ModelProvider).where(
            ModelProvider.id == provider_id,
            ModelProvider.tenant_id == principal.tenant.id,
        )
    )
    if provider is None:
        raise _not_found("model_provider")
    return provider


def _get_knowledge_base(
    db: Session,
    principal: Principal,
    knowledge_base_id: UUID,
) -> KnowledgeBase:
    knowledge_base = db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.tenant_id == principal.tenant.id,
        )
    )
    if knowledge_base is None:
        raise _not_found("knowledge_base")
    return knowledge_base


def _validate_provider(
    db: Session,
    principal: Principal,
    provider_id: UUID | None,
) -> ModelProvider | None:
    if provider_id is None:
        return None
    return _get_provider(db, principal, provider_id)


def _validate_platform_model(
    db: Session,
    principal: Principal,
    platform_model_id: UUID | None,
) -> PlatformModel:
    if platform_model_id is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "platform_model_required",
                "message": "平台模型模式必须选择一个平台模型",
            },
        )
    policy = db.get(TenantBillingPolicy, principal.tenant.id)
    currency = policy.currency if policy is not None else "CNY"
    model = db.scalar(
        select(PlatformModel).where(
            PlatformModel.id == platform_model_id,
            PlatformModel.enabled.is_(True),
            PlatformModel.provider_type != "anthropic",
        )
    )
    if model is None:
        raise _not_found("platform_model")
    now = datetime.now(UTC)
    price = db.scalar(
        select(PlatformModelPrice.id).where(
            PlatformModelPrice.platform_model_id == model.id,
            PlatformModelPrice.currency == currency,
            PlatformModelPrice.effective_from <= now,
            or_(
                PlatformModelPrice.effective_until.is_(None),
                PlatformModelPrice.effective_until > now,
            ),
        ).limit(1)
    )
    if price is None:
        raise _conflict(
            "platform_model_price_unavailable",
            "该平台模型尚无当前生效的价格版本，暂时不能绑定",
        )
    return model


def _validate_knowledge_bases(
    db: Session,
    principal: Principal,
    knowledge_base_ids: list[UUID],
) -> list[UUID]:
    unique_ids = list(dict.fromkeys(knowledge_base_ids))
    if not unique_ids:
        return []
    found = set(
        db.scalars(
            select(KnowledgeBase.id).where(
                KnowledgeBase.tenant_id == principal.tenant.id,
                KnowledgeBase.id.in_(unique_ids),
            )
        ).all()
    )
    if found != set(unique_ids):
        raise _not_found("knowledge_base")
    return unique_ids


def _persona_read(db: Session, principal: Principal, persona: Persona) -> PersonaRead:
    provider_name = None
    platform_model_name = None
    if persona.provider_id is not None:
        provider_name = db.scalar(
            select(ModelProvider.name).where(
                ModelProvider.id == persona.provider_id,
                ModelProvider.tenant_id == principal.tenant.id,
            )
        )
    if persona.platform_model_id is not None:
        platform_model_name = db.scalar(
            select(PlatformModel.display_name).where(
                PlatformModel.id == persona.platform_model_id
            )
        )
    knowledge_base_ids = db.scalars(
        select(PersonaKnowledgeBase.knowledge_base_id).where(
            PersonaKnowledgeBase.tenant_id == principal.tenant.id,
            PersonaKnowledgeBase.persona_id == persona.id,
        )
    ).all()
    return PersonaRead(
        id=persona.id,
        name=persona.name,
        slug=persona.slug,
        description=persona.description,
        system_prompt=persona.system_prompt,
        model_source=persona.model_source,
        provider_id=persona.provider_id,
        provider_name=provider_name,
        platform_model_id=persona.platform_model_id,
        platform_model_name=platform_model_name,
        model_name=persona.model_name,
        temperature=persona.temperature,
        top_p=persona.top_p,
        max_tokens=persona.max_tokens,
        enabled=persona.enabled,
        knowledge_base_ids=list(knowledge_base_ids),
        created_at=persona.created_at,
        updated_at=persona.updated_at,
    )


@router.get("/model-providers", response_model=list[ModelProviderRead])
def list_model_providers(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[ModelProviderRead]:
    providers = db.scalars(
        select(ModelProvider)
        .where(ModelProvider.tenant_id == principal.tenant.id)
        .order_by(ModelProvider.created_at.desc())
    ).all()
    return [_provider_read(provider) for provider in providers]


@router.post(
    "/model-providers",
    response_model=ModelProviderRead,
    status_code=status.HTTP_201_CREATED,
)
def create_model_provider(
    payload: ModelProviderCreate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> ModelProviderRead:
    try:
        ciphertext = encrypt_provider_api_key(payload.api_key)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "encryption_not_configured", "message": str(exc)},
        ) from None

    provider = ModelProvider(
        id=uuid4(),
        tenant_id=principal.tenant.id,
        name=payload.name,
        provider_type=payload.provider_type,
        base_url=payload.base_url,
        api_key_ciphertext=ciphertext,
        api_key_last4=payload.api_key[-4:],
        enabled=payload.enabled,
    )
    db.add(provider)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict(
            "model_provider_name_exists",
            "同一工作空间内的供应商名称不能重复",
        ) from None
    db.refresh(provider)
    return _provider_read(provider)


@router.patch("/model-providers/{provider_id}", response_model=ModelProviderRead)
def update_model_provider(
    provider_id: UUID,
    payload: ModelProviderUpdate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> ModelProviderRead:
    provider = _get_provider(db, principal, provider_id)
    updates = payload.model_dump(exclude_unset=True)
    api_key = updates.pop("api_key", None)
    clear_api_key = updates.pop("clear_api_key", False)
    _reject_null_updates(updates)

    if clear_api_key and api_key is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "conflicting_api_key_update", "message": "不能同时设置和清除 API Key"},
        )
    if api_key is not None:
        try:
            provider.api_key_ciphertext = encrypt_provider_api_key(api_key)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "encryption_not_configured", "message": str(exc)},
            ) from None
        provider.api_key_last4 = api_key[-4:]
    elif clear_api_key:
        provider.api_key_ciphertext = None
        provider.api_key_last4 = None

    for field, value in updates.items():
        setattr(provider, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict(
            "model_provider_name_exists",
            "同一工作空间内的供应商名称不能重复",
        ) from None
    db.refresh(provider)
    return _provider_read(provider)


@router.delete("/model-providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_provider(
    provider_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
):
    provider = _get_provider(db, principal, provider_id)
    used = db.scalar(
        select(Persona.id).where(
            Persona.tenant_id == principal.tenant.id,
            Persona.provider_id == provider.id,
        ).limit(1)
    )
    if used is not None:
        raise _conflict("model_provider_in_use", "该供应商仍被人格使用，请先修改相关人格配置")
    db.delete(provider)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict(
            "model_provider_in_use",
            "该供应商仍被人格使用，请先修改相关人格配置",
        ) from None


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseRead])
def list_knowledge_bases(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[KnowledgeBaseRead]:
    records = db.scalars(
        select(KnowledgeBase)
        .where(KnowledgeBase.tenant_id == principal.tenant.id)
        .order_by(KnowledgeBase.created_at.desc())
    ).all()
    return [
        KnowledgeBaseRead(
            id=item.id,
            name=item.name,
            description=item.description,
            documents_count=0,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        for item in records
    ]


@router.post(
    "/knowledge-bases",
    response_model=KnowledgeBaseRead,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> KnowledgeBaseRead:
    record = KnowledgeBase(
        id=uuid4(),
        tenant_id=principal.tenant.id,
        name=payload.name,
        description=payload.description,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict(
            "knowledge_base_name_exists",
            "同一工作空间内的知识库名称不能重复",
        ) from None
    db.refresh(record)
    return KnowledgeBaseRead(
        id=record.id,
        name=record.name,
        description=record.description,
        documents_count=0,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.patch("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseRead)
def update_knowledge_base(
    knowledge_base_id: UUID,
    payload: KnowledgeBaseUpdate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> KnowledgeBaseRead:
    record = _get_knowledge_base(db, principal, knowledge_base_id)
    updates = payload.model_dump(exclude_unset=True)
    _reject_null_updates(updates)
    for field, value in updates.items():
        setattr(record, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict(
            "knowledge_base_name_exists",
            "同一工作空间内的知识库名称不能重复",
        ) from None
    db.refresh(record)
    return KnowledgeBaseRead(
        id=record.id,
        name=record.name,
        description=record.description,
        documents_count=0,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.delete("/knowledge-bases/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_base(
    knowledge_base_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
):
    record = _get_knowledge_base(db, principal, knowledge_base_id)
    db.execute(
        delete(PersonaKnowledgeBase).where(
            PersonaKnowledgeBase.tenant_id == principal.tenant.id,
            PersonaKnowledgeBase.knowledge_base_id == record.id,
        )
    )
    db.delete(record)
    db.commit()


@router.get("/personas", response_model=list[PersonaRead])
def list_personas(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[PersonaRead]:
    records = db.scalars(
        select(Persona)
        .where(Persona.tenant_id == principal.tenant.id)
        .order_by(Persona.created_at.desc())
    ).all()
    return [_persona_read(db, principal, persona) for persona in records]


@router.post("/personas", response_model=PersonaRead, status_code=status.HTTP_201_CREATED)
def create_persona(
    payload: PersonaCreate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> PersonaRead:
    if payload.model_source == "platform":
        platform_model = _validate_platform_model(db, principal, payload.platform_model_id)
        if payload.provider_id is not None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "model_source_conflict",
                    "message": "平台模型模式不能绑定用户自己的模型供应商",
                },
            )
        model_name = platform_model.upstream_model_name
        provider_id = None
        platform_model_id = platform_model.id
    else:
        _validate_provider(db, principal, payload.provider_id)
        if payload.model_name is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "model_name_required", "message": "BYOK 模式必须填写上游模型名称"},
            )
        model_name = payload.model_name
        provider_id = payload.provider_id
        platform_model_id = None
    knowledge_base_ids = _validate_knowledge_bases(
        db, principal, payload.knowledge_base_ids
    )
    persona = Persona(
        id=uuid4(),
        tenant_id=principal.tenant.id,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        system_prompt=payload.system_prompt,
        provider_id=provider_id,
        model_source=payload.model_source,
        platform_model_id=platform_model_id,
        model_name=model_name,
        temperature=payload.temperature,
        top_p=payload.top_p,
        max_tokens=payload.max_tokens,
        enabled=payload.enabled,
    )
    db.add(persona)
    try:
        db.flush()
        if knowledge_base_ids:
            db.execute(
                insert(PersonaKnowledgeBase),
                [
                    {
                        "tenant_id": principal.tenant.id,
                        "persona_id": persona.id,
                        "knowledge_base_id": knowledge_base_id,
                    }
                    for knowledge_base_id in knowledge_base_ids
                ],
            )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict("persona_slug_exists", "同一工作空间内的人格调用标识不能重复") from None
    db.refresh(persona)
    return _persona_read(db, principal, persona)


@router.get("/personas/{persona_id}", response_model=PersonaRead)
def get_persona(
    persona_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> PersonaRead:
    persona = db.scalar(
        select(Persona).where(
            Persona.id == persona_id,
            Persona.tenant_id == principal.tenant.id,
        )
    )
    if persona is None:
        raise _not_found("persona")
    return _persona_read(db, principal, persona)


@router.patch("/personas/{persona_id}", response_model=PersonaRead)
def update_persona(
    persona_id: UUID,
    payload: PersonaUpdate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> PersonaRead:
    persona = db.scalar(
        select(Persona).where(
            Persona.id == persona_id,
            Persona.tenant_id == principal.tenant.id,
        )
    )
    if persona is None:
        raise _not_found("persona")

    updates = payload.model_dump(exclude_unset=True, exclude={"knowledge_base_ids"})
    _reject_null_updates(
        updates,
        nullable_fields={"provider_id", "platform_model_id"},
    )
    target_source = updates.get("model_source", persona.model_source)
    if target_source == "platform":
        target_model_id = updates.get("platform_model_id", persona.platform_model_id)
        platform_model = _validate_platform_model(db, principal, target_model_id)
        if updates.get("provider_id") is not None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "model_source_conflict",
                    "message": "平台模型模式不能绑定用户自己的模型供应商",
                },
            )
        if "model_name" in updates and updates["model_name"] != platform_model.upstream_model_name:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "platform_model_name_managed",
                    "message": "平台模型的上游模型名称由平台统一管理",
                },
            )
        updates["model_source"] = "platform"
        updates["platform_model_id"] = platform_model.id
        updates["provider_id"] = None
        updates["model_name"] = platform_model.upstream_model_name
    else:
        target_provider_id = updates.get("provider_id", persona.provider_id)
        if target_provider_id is not None:
            _validate_provider(db, principal, target_provider_id)
        updates["model_source"] = "byok"
        updates["platform_model_id"] = None
        updates["provider_id"] = target_provider_id
        if updates.get("model_name", persona.model_name) is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "model_name_required", "message": "BYOK 模式必须填写上游模型名称"},
            )
    if "knowledge_base_ids" in payload.model_fields_set:
        knowledge_base_ids = _validate_knowledge_bases(
            db, principal, payload.knowledge_base_ids or []
        )
        db.execute(
            delete(PersonaKnowledgeBase).where(
                PersonaKnowledgeBase.tenant_id == principal.tenant.id,
                PersonaKnowledgeBase.persona_id == persona.id,
            )
        )
        if knowledge_base_ids:
            db.execute(
                insert(PersonaKnowledgeBase),
                [
                    {
                        "tenant_id": principal.tenant.id,
                        "persona_id": persona.id,
                        "knowledge_base_id": knowledge_base_id,
                    }
                    for knowledge_base_id in knowledge_base_ids
                ],
            )

    for field, value in updates.items():
        setattr(persona, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict("persona_slug_exists", "同一工作空间内的人格调用标识不能重复") from None
    db.refresh(persona)
    return _persona_read(db, principal, persona)


@router.delete("/personas/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_persona(
    persona_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
):
    persona = db.scalar(
        select(Persona).where(
            Persona.id == persona_id,
            Persona.tenant_id == principal.tenant.id,
        )
    )
    if persona is None:
        raise _not_found("persona")
    db.execute(
        delete(PersonaKnowledgeBase).where(
            PersonaKnowledgeBase.tenant_id == principal.tenant.id,
            PersonaKnowledgeBase.persona_id == persona.id,
        )
    )
    db.delete(persona)
    db.commit()
