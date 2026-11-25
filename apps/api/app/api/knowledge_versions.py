from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, DatabaseSession, Principal
from app.knowledge.indexing import IndexConflict, create_index_build, switch_index_release
from app.knowledge.legacy_revisions import list_revisions, restore_revision
from app.models.catalog import KnowledgeBase
from app.models.knowledge import KnowledgeIndexRelease, KnowledgeIndexState
from app.schemas.knowledge import (
    DocumentHistoryRead,
    DocumentIndexStateRead,
    DocumentRestoreRequest,
    DocumentRevisionRead,
    IndexBuildRequest,
    IndexPublishRequest,
    IndexRollbackRequest,
    IndexStateRead,
    IndexVersionConfigRead,
    IndexVersionRead,
    IndexVersionsRead,
)

router = APIRouter(
    prefix="/knowledge-bases/{knowledge_base_id}/index-versions", tags=["知识库索引版本"]
)

document_history_router = APIRouter(
    prefix="/knowledge-bases/{knowledge_base_id}/documents/{document_id}/index-history",
    tags=["文档索引历史"],
)


def _authorize(db, principal: Principal, knowledge_base_id: UUID) -> None:
    if principal.membership.role != "owner":
        raise HTTPException(
            403, detail={"code": "index_owner_required", "message": "需要工作空间所有者权限"}
        )
    kb = db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.tenant_id == principal.tenant.id, KnowledgeBase.id == knowledge_base_id
        )
    )
    if kb is None:
        raise HTTPException(
            404, detail={"code": "knowledge_base_not_found", "message": "知识库不存在"}
        )
    if not kb.enabled:
        raise HTTPException(
            409, detail={"code": "knowledge_base_disabled", "message": "知识库已停用"}
        )


def _version_read(release: KnowledgeIndexRelease) -> IndexVersionRead:
    # Allow reading a newer parser's metadata after reverting the application image.
    # The build request schema separately restricts executable parser versions.
    public_config = {key: release.config_json[key] for key in IndexVersionConfigRead.model_fields}
    return IndexVersionRead(
        id=release.id,
        revision=release.revision,
        status=release.status,
        expected_generation=release.expected_generation,
        config=IndexVersionConfigRead(**public_config),
        created_at=release.created_at,
        published_at=release.published_at,
    )


def _scoped_releases(tenant_id: UUID, knowledge_base_id: UUID):
    return select(KnowledgeIndexRelease).where(
        KnowledgeIndexRelease.tenant_id == tenant_id,
        KnowledgeIndexRelease.knowledge_base_id == knowledge_base_id,
    )


def _version_not_found() -> HTTPException:
    return HTTPException(
        404, detail={"code": "index_version_not_found", "message": "索引版本不存在"}
    )


def _get_release(db, tenant_id, knowledge_base_id, release_id):
    release = db.scalar(
        _scoped_releases(tenant_id, knowledge_base_id).where(KnowledgeIndexRelease.id == release_id)
    )
    if release is None:
        raise _version_not_found()
    return release


def _conflict(exc: IndexConflict) -> HTTPException:
    return HTTPException(
        409, detail={"code": str(exc), "message": "索引状态已变化或未满足操作条件"}
    )


@router.get("", response_model=IndexVersionsRead)
def list_index_versions(
    knowledge_base_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> IndexVersionsRead:
    _authorize(db, principal, knowledge_base_id)
    tenant_id = principal.tenant.id
    state = db.get(KnowledgeIndexState, (tenant_id, knowledge_base_id))
    releases = db.scalars(
        _scoped_releases(tenant_id, knowledge_base_id)
        .order_by(KnowledgeIndexRelease.revision.desc())
        .offset(offset)
        .limit(limit)
    )
    return IndexVersionsRead(
        generation=state.generation if state else 0,
        active_release_id=state.active_release_id if state else None,
        versions=[_version_read(item) for item in releases],
    )


@router.get("/{release_id}", response_model=IndexVersionRead)
def get_index_version(
    knowledge_base_id: UUID,
    release_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> IndexVersionRead:
    _authorize(db, principal, knowledge_base_id)
    release = _get_release(db, principal.tenant.id, knowledge_base_id, release_id)
    return _version_read(release)


@router.post("", response_model=IndexVersionRead, status_code=status.HTTP_202_ACCEPTED)
def build_index_version(
    knowledge_base_id: UUID,
    payload: IndexBuildRequest,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> IndexVersionRead:
    _authorize(db, principal, knowledge_base_id)
    try:
        release = create_index_build(
            db,
            tenant_id=principal.tenant.id,
            knowledge_base_id=knowledge_base_id,
            request_key=payload.request_key,
            expected_generation=payload.expected_generation,
            config=payload.config.model_dump(exclude_none=True),
        )
        db.commit()
    except IndexConflict as exc:
        raise _conflict(exc) from None
    except LookupError:
        raise _version_not_found() from None
    except ValueError as exc:
        if str(exc) in {
            "chunk_size_out_of_range",
            "chunk_overlap_out_of_range",
            "unsupported_index_parser",
            "unknown_index_configuration",
            "invalid_request_key",
        }:
            raise HTTPException(
                422, detail={"code": str(exc), "message": "索引配置无效，请检查切片长度和重叠范围"}
            ) from None
        raise HTTPException(
            409,
            detail={
                "code": "index_embedding_unavailable",
                "message": "当前知识库的 Embedding 配置不可用",
            },
        ) from None
    return _version_read(release)


def _switch(db, tenant_id, knowledge_base_id, release_id, expected_generation, *, rollback):
    if release_id is not None:
        _get_release(db, tenant_id, knowledge_base_id, release_id)
    try:
        state = switch_index_release(
            db,
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            release_id=release_id,
            expected_generation=expected_generation,
            rollback=rollback,
        )
        db.commit()
    except IndexConflict as exc:
        raise _conflict(exc) from None
    except LookupError:
        raise _version_not_found() from None
    except ValueError:
        raise HTTPException(
            409,
            detail={
                "code": "index_embedding_unavailable",
                "message": "发布版本的 Embedding 配置不可用或已变更",
            },
        ) from None
    return IndexStateRead(generation=state.generation, active_release_id=state.active_release_id)


@router.post("/{release_id}/publish", response_model=IndexStateRead)
def publish_index_version(
    knowledge_base_id: UUID,
    release_id: UUID,
    payload: IndexPublishRequest,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> IndexStateRead:
    _authorize(db, principal, knowledge_base_id)
    return _switch(
        db,
        principal.tenant.id,
        knowledge_base_id,
        release_id,
        payload.expected_generation,
        rollback=False,
    )


@router.post("/rollback", response_model=IndexStateRead)
def rollback_index_version(
    knowledge_base_id: UUID,
    payload: IndexRollbackRequest,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> IndexStateRead:
    _authorize(db, principal, knowledge_base_id)
    return _switch(
        db,
        principal.tenant.id,
        knowledge_base_id,
        payload.target_release_id,
        payload.expected_generation,
        rollback=True,
    )


@document_history_router.get("", response_model=DocumentHistoryRead)
def list_document_history(
    knowledge_base_id: UUID,
    document_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentHistoryRead:
    _authorize(db, principal, knowledge_base_id)
    try:
        history = list_revisions(
            db, principal.tenant.id, knowledge_base_id, document_id, limit, offset
        )
    except LookupError:
        raise HTTPException(
            404, detail={"code": "document_not_found", "message": "文档不存在"}
        ) from None
    except IndexConflict as exc:
        raise _conflict(exc) from None
    return DocumentHistoryRead(
        active_index_version=history["active_index_version"],
        generation=history["generation"],
        revisions=[
            DocumentRevisionRead(
                version=item.index_version,
                created_at=item.created_at,
                chunk_count=item.chunk_count,
                profile_origin=item.profile_origin,
            )
            for item in history["revisions"]
        ],
    )


@document_history_router.post("/{version}/restore", response_model=DocumentIndexStateRead)
def restore_document_index(
    knowledge_base_id: UUID,
    document_id: UUID,
    version: Annotated[int, Path(ge=1, le=2147483647)],
    payload: DocumentRestoreRequest,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> DocumentIndexStateRead:
    _authorize(db, principal, knowledge_base_id)
    try:
        state = restore_revision(
            db,
            principal.tenant.id,
            knowledge_base_id,
            document_id,
            version,
            payload.expected_active_index_version,
            payload.expected_generation,
        )
        db.commit()
    except LookupError:
        raise HTTPException(
            404,
            detail={"code": "document_revision_not_found", "message": "文档或历史索引版本不存在"},
        ) from None
    except IndexConflict as exc:
        raise _conflict(exc) from None
    return DocumentIndexStateRead(**state)
