from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, DatabaseSession, Principal
from app.core.secret_encryption import encrypt_provider_api_key
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
    if persona.provider_id is not None:
        provider_name = db.scalar(
            select(ModelProvider.name).where(
                ModelProvider.id == persona.provider_id,
                ModelProvider.tenant_id == principal.tenant.id,
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
        provider_id=persona.provider_id,
        provider_name=provider_name,
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
        raise _conflict("model_provider_name_exists", "同一工作空间内的供应商名称不能重复") from None
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
        raise _conflict("model_provider_name_exists", "同一工作空间内的供应商名称不能重复") from None
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
    db.commit()


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
        raise _conflict("knowledge_base_name_exists", "同一工作空间内的知识库名称不能重复") from None
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
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(record, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict("knowledge_base_name_exists", "同一工作空间内的知识库名称不能重复") from None
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
    _validate_provider(db, principal, payload.provider_id)
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
        provider_id=payload.provider_id,
        model_name=payload.model_name,
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
    if "provider_id" in updates:
        _validate_provider(db, principal, updates["provider_id"])
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
