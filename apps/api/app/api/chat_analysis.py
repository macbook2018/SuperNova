from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.billing.credentials import resolve_platform_api_key
from app.core.secret_encryption import decrypt_provider_api_key
from app.knowledge.network import validate_public_service_url
from app.knowledge.storage import put_object
from app.models.billing import PlatformModel
from app.models.catalog import KnowledgeBase, ModelProvider, Persona
from app.models.chat_analysis import (
    ChatAnalysisJob,
    ChatAnalysisReviewEvent,
    FaqCandidate,
    PersonaDraft,
)
from app.models.chat_import import ChatImportFile, ChatImportJob, ChatSource
from app.models.knowledge import KnowledgeDocument, KnowledgeIngestionJob
from app.schemas.chat_analysis import (
    ChatAnalysisBundle,
    ChatAnalysisJobRead,
    ChatAnalysisStart,
    FaqCandidateRead,
    FaqCandidateUpdate,
    FaqPublishRequest,
    PersonaDraftApply,
    PersonaDraftRead,
    PersonaDraftUpdate,
    ReviewDecision,
)

router = APIRouter(tags=["Persona 学习与 FAQ 审核"])


def _not_found(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": code, "message": message})


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


def _analysis_job_read(job: ChatAnalysisJob) -> ChatAnalysisJobRead:
    return ChatAnalysisJobRead(
        id=job.id,
        import_job_id=job.import_job_id,
        source_account_id=job.source_id,
        model_source=job.model_source,
        model_name=job.model_name,
        status=job.status,
        attempts=job.attempts,
        owner_message_count=job.owner_message_count,
        faq_candidate_count=job.faq_candidate_count,
        input_tokens=job.input_tokens,
        output_tokens=job.output_tokens,
        request_count=job.request_count,
        last_error=job.last_error,
        completed_at=job.completed_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _persona_draft_read(draft: PersonaDraft) -> PersonaDraftRead:
    return PersonaDraftRead(
        id=draft.id,
        source_account_id=draft.source_id,
        analysis_job_id=draft.analysis_job_id,
        status=draft.status,
        summary=draft.summary,
        profile_json=draft.profile_json,
        system_prompt_suggestion=draft.system_prompt_suggestion,
        evidence_message_count=draft.evidence_message_count,
        applied_persona_id=draft.applied_persona_id,
        approved_at=draft.approved_at,
        applied_at=draft.applied_at,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def _faq_candidate_read(candidate: FaqCandidate) -> FaqCandidateRead:
    return FaqCandidateRead(
        id=candidate.id,
        source_account_id=candidate.source_id,
        analysis_job_id=candidate.analysis_job_id,
        status=candidate.status,
        title=candidate.title,
        category=candidate.category,
        canonical_question=candidate.canonical_question,
        question_variants=candidate.question_variants or [],
        answer=candidate.answer,
        confidence=candidate.confidence,
        occurrence_count=candidate.occurrence_count,
        effective_date=candidate.effective_date,
        published_document_id=candidate.published_document_id,
        approved_at=candidate.approved_at,
        published_at=candidate.published_at,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )


def _event_hash(value: dict | None) -> str | None:
    if value is None:
        return None
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _record_event(
    db,
    *,
    principal,
    source_id: UUID,
    entity_type: str,
    entity_id: UUID,
    action: str,
    before: dict | None = None,
    after: dict | None = None,
    note: str | None = None,
) -> None:
    db.add(
        ChatAnalysisReviewEvent(
            id=uuid4(),
            tenant_id=principal.tenant.id,
            source_id=source_id,
            actor_user_id=principal.user.id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            before_hash=_event_hash(before),
            after_hash=_event_hash(after),
            note=note,
        )
    )


def _get_import_job(db, tenant_id: UUID, job_id: UUID) -> ChatImportJob:
    job = db.scalar(
        select(ChatImportJob).where(
            ChatImportJob.id == job_id,
            ChatImportJob.tenant_id == tenant_id,
        )
    )
    if job is None:
        raise _not_found("chat_import_job_not_found", "导入任务不存在")
    return job


def _get_persona_draft(db, tenant_id: UUID, draft_id: UUID) -> PersonaDraft:
    draft = db.scalar(
        select(PersonaDraft).where(
            PersonaDraft.id == draft_id,
            PersonaDraft.tenant_id == tenant_id,
        )
    )
    if draft is None:
        raise _not_found("persona_draft_not_found", "Persona 草稿不存在")
    return draft


def _get_faq_candidate(db, tenant_id: UUID, candidate_id: UUID) -> FaqCandidate:
    candidate = db.scalar(
        select(FaqCandidate).where(
            FaqCandidate.id == candidate_id,
            FaqCandidate.tenant_id == tenant_id,
        )
    )
    if candidate is None:
        raise _not_found("faq_candidate_not_found", "FAQ 候选知识不存在")
    return candidate


def _validate_analysis_model(db, principal, payload: ChatAnalysisStart) -> str:
    if payload.model_source == "platform":
        model = db.get(PlatformModel, payload.platform_model_id)
        if model is None or not model.enabled or model.provider_type == "anthropic":
            raise _not_found("platform_model_not_found", "可用的平台模型不存在")
        if not model.credential_ref:
            raise _conflict("platform_credentials_missing", "平台模型尚未配置服务端凭据")
        try:
            resolve_platform_api_key(model.credential_ref)
            validate_public_service_url(model.base_url)
        except HTTPException:
            raise
        except ValueError:
            raise _conflict("platform_model_endpoint_invalid", "平台模型接口地址不可用") from None
        return model.upstream_model_name

    provider = db.scalar(
        select(ModelProvider).where(
            ModelProvider.id == payload.provider_id,
            ModelProvider.tenant_id == principal.tenant.id,
        )
    )
    if provider is None or not provider.enabled or not provider.api_key_ciphertext:
        raise _conflict("analysis_provider_unavailable", "所选模型供应商未启用或未配置 API Key")
    if provider.provider_type == "anthropic":
        raise _conflict("analysis_provider_not_supported", "人格学习暂不支持 Anthropic 原生协议")
    try:
        decrypt_provider_api_key(provider.api_key_ciphertext)
        validate_public_service_url(provider.base_url)
    except RuntimeError:
        raise _conflict(
            "analysis_provider_credentials_unavailable",
            "模型供应商凭据无法解密",
        ) from None
    except ValueError:
        raise _conflict("analysis_provider_endpoint_invalid", "模型供应商接口地址不可用") from None
    return payload.model_name or ""


@router.post(
    "/chat-imports/{job_id}/analyze",
    response_model=ChatAnalysisJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_chat_analysis(
    job_id: UUID,
    payload: ChatAnalysisStart,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> ChatAnalysisJobRead:
    import_job = _get_import_job(db, principal.tenant.id, job_id)
    source = db.scalar(
        select(ChatSource).where(
            ChatSource.id == import_job.source_id,
            ChatSource.tenant_id == principal.tenant.id,
            ChatSource.status == "active",
        )
    )
    if source is None:
        raise _not_found("source_account_not_found", "来源账号不存在或已停用")
    if source.identity_confirmed_at is None or not source.target_sender_ref:
        raise _conflict("source_identity_not_confirmed", "请先明确确认当前微信账号的本人身份")
    if import_job.status != "ready_for_analysis":
        raise _conflict("chat_import_not_ready_for_analysis", "该导入任务尚未完成解析或已结束")

    existing = db.scalar(
        select(ChatAnalysisJob).where(
            ChatAnalysisJob.tenant_id == principal.tenant.id,
            ChatAnalysisJob.source_id == source.id,
            ChatAnalysisJob.import_job_id == import_job.id,
        )
    )
    if existing is not None:
        if existing.status in {"queued", "running", "retry_wait", "succeeded"}:
            return _analysis_job_read(existing)
        raise _conflict(
            "chat_analysis_terminal",
            "该分析任务已失败或取消；临时聊天文件不会保留，请重新上传后再分析",
        )

    now = datetime.now(UTC)
    files_count = db.scalar(
        select(ChatImportFile.id)
        .where(
            ChatImportFile.tenant_id == principal.tenant.id,
            ChatImportFile.source_id == source.id,
            ChatImportFile.job_id == import_job.id,
            ChatImportFile.expires_at > now,
        )
        .limit(1)
    )
    if files_count is None:
        raise _conflict("chat_import_upload_expired", "临时聊天文件已过期，请重新上传")

    model_name = _validate_analysis_model(db, principal, payload)
    analysis_job = ChatAnalysisJob(
        id=uuid4(),
        tenant_id=principal.tenant.id,
        source_id=source.id,
        import_job_id=import_job.id,
        owner_sender_ref=source.target_sender_ref,
        model_source=payload.model_source,
        provider_id=payload.provider_id if payload.model_source == "byok" else None,
        platform_model_id=payload.platform_model_id if payload.model_source == "platform" else None,
        model_name=model_name,
        status="queued",
        attempts=0,
        max_attempts=3,
    )
    db.add(analysis_job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(ChatAnalysisJob).where(
                ChatAnalysisJob.tenant_id == principal.tenant.id,
                ChatAnalysisJob.source_id == source.id,
                ChatAnalysisJob.import_job_id == import_job.id,
            )
        )
        if existing is not None:
            return _analysis_job_read(existing)
        raise _conflict("chat_analysis_conflict", "分析任务创建冲突，请刷新后重试") from None
    db.refresh(analysis_job)
    return _analysis_job_read(analysis_job)


@router.get(
    "/chat-imports/{job_id}/analysis",
    response_model=ChatAnalysisBundle,
)
def get_chat_analysis_bundle(
    job_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> ChatAnalysisBundle:
    import_job = _get_import_job(db, principal.tenant.id, job_id)
    analysis_job = db.scalar(
        select(ChatAnalysisJob).where(
            ChatAnalysisJob.tenant_id == principal.tenant.id,
            ChatAnalysisJob.source_id == import_job.source_id,
            ChatAnalysisJob.import_job_id == import_job.id,
        )
    )
    if analysis_job is None:
        return ChatAnalysisBundle(analysis_job=None, persona_draft=None, faq_candidates=[])

    draft = db.scalar(
        select(PersonaDraft).where(
            PersonaDraft.tenant_id == principal.tenant.id,
            PersonaDraft.analysis_job_id == analysis_job.id,
        )
    )
    candidates = db.scalars(
        select(FaqCandidate)
        .where(
            FaqCandidate.tenant_id == principal.tenant.id,
            FaqCandidate.analysis_job_id == analysis_job.id,
        )
        .order_by(FaqCandidate.occurrence_count.desc(), FaqCandidate.created_at.asc())
    ).all()
    return ChatAnalysisBundle(
        analysis_job=_analysis_job_read(analysis_job),
        persona_draft=_persona_draft_read(draft) if draft is not None else None,
        faq_candidates=[_faq_candidate_read(item) for item in candidates],
    )


@router.patch("/persona-drafts/{draft_id}", response_model=PersonaDraftRead)
def update_persona_draft(
    draft_id: UUID,
    payload: PersonaDraftUpdate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> PersonaDraftRead:
    draft = _get_persona_draft(db, principal.tenant.id, draft_id)
    if draft.status not in {"pending_review", "approved"}:
        raise _conflict("persona_draft_not_editable", "当前 Persona 草稿状态不允许编辑")
    before = {
        "summary": draft.summary,
        "profile_json": draft.profile_json,
        "system_prompt_suggestion": draft.system_prompt_suggestion,
    }
    updates = payload.model_dump(exclude_unset=True)
    if any(value is None for value in updates.values()):
        raise HTTPException(
            status_code=422,
            detail={"code": "null_not_allowed", "message": "Persona 草稿字段不允许设置为 null"},
        )
    if "summary" in updates:
        draft.summary = updates["summary"]
    if "profile_json" in updates:
        draft.profile_json = updates["profile_json"]
    if "system_prompt_suggestion" in updates:
        draft.system_prompt_suggestion = updates["system_prompt_suggestion"]
    draft.status = "pending_review"
    draft.approved_at = None
    after = {
        "summary": draft.summary,
        "profile_json": draft.profile_json,
        "system_prompt_suggestion": draft.system_prompt_suggestion,
    }
    _record_event(
        db,
        principal=principal,
        source_id=draft.source_id,
        entity_type="persona_draft",
        entity_id=draft.id,
        action="edited",
        before=before,
        after=after,
    )
    db.commit()
    db.refresh(draft)
    return _persona_draft_read(draft)


@router.post("/persona-drafts/{draft_id}/approve", response_model=PersonaDraftRead)
def approve_persona_draft(
    draft_id: UUID,
    payload: ReviewDecision,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> PersonaDraftRead:
    draft = _get_persona_draft(db, principal.tenant.id, draft_id)
    if draft.status != "pending_review":
        raise _conflict("persona_draft_not_reviewable", "只有待审核的 Persona 草稿可以批准")
    draft.status = "approved"
    draft.approved_at = datetime.now(UTC)
    _record_event(
        db,
        principal=principal,
        source_id=draft.source_id,
        entity_type="persona_draft",
        entity_id=draft.id,
        action="approved",
        after={"status": draft.status},
        note=payload.note,
    )
    db.commit()
    db.refresh(draft)
    return _persona_draft_read(draft)


@router.post("/persona-drafts/{draft_id}/reject", response_model=PersonaDraftRead)
def reject_persona_draft(
    draft_id: UUID,
    payload: ReviewDecision,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> PersonaDraftRead:
    draft = _get_persona_draft(db, principal.tenant.id, draft_id)
    if draft.status not in {"pending_review", "approved"}:
        raise _conflict("persona_draft_not_rejectable", "当前 Persona 草稿状态不允许拒绝")
    draft.status = "rejected"
    draft.rejected_at = datetime.now(UTC)
    _record_event(
        db,
        principal=principal,
        source_id=draft.source_id,
        entity_type="persona_draft",
        entity_id=draft.id,
        action="rejected",
        before={"status": "pending_review"},
        after={"status": draft.status},
        note=payload.note,
    )
    db.commit()
    db.refresh(draft)
    return _persona_draft_read(draft)


@router.post("/persona-drafts/{draft_id}/apply", response_model=PersonaDraftRead)
def apply_persona_draft(
    draft_id: UUID,
    payload: PersonaDraftApply,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> PersonaDraftRead:
    draft = _get_persona_draft(db, principal.tenant.id, draft_id)
    if draft.status != "approved":
        raise _conflict("persona_draft_not_approved", "必须先审核批准 Persona 草稿")
    persona = db.scalar(
        select(Persona).where(
            Persona.id == payload.persona_id,
            Persona.tenant_id == principal.tenant.id,
        )
    )
    if persona is None:
        raise _not_found("persona_not_found", "目标 Persona 不存在")
    if persona.updated_at != payload.expected_updated_at:
        raise _conflict(
            "persona_changed_since_review",
            "目标 Persona 在读取后已发生变化，请刷新后重新确认应用方式",
        )

    before_prompt = persona.system_prompt
    if payload.mode == "replace":
        next_prompt = draft.system_prompt_suggestion
    else:
        next_prompt = (
            before_prompt.rstrip()
            + "\n\n## 从聊天样本学习的表达风格\n"
            + draft.system_prompt_suggestion
        )
    if len(next_prompt) > 20000:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "persona_prompt_too_large",
                "message": "应用后的 Persona 提示词超过 20000 字符",
            },
        )

    persona.system_prompt = next_prompt
    persona.updated_at = datetime.now(UTC)
    draft.status = "applied"
    draft.applied_persona_id = persona.id
    draft.applied_at = datetime.now(UTC)
    _record_event(
        db,
        principal=principal,
        source_id=draft.source_id,
        entity_type="persona_draft",
        entity_id=draft.id,
        action="applied",
        before={"system_prompt_sha256": hashlib.sha256(before_prompt.encode()).hexdigest()},
        after={
            "persona_id": str(persona.id),
            "mode": payload.mode,
            "system_prompt_sha256": hashlib.sha256(next_prompt.encode()).hexdigest(),
        },
    )
    db.commit()
    db.refresh(draft)
    return _persona_draft_read(draft)


@router.patch("/faq-candidates/{candidate_id}", response_model=FaqCandidateRead)
def update_faq_candidate(
    candidate_id: UUID,
    payload: FaqCandidateUpdate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> FaqCandidateRead:
    candidate = _get_faq_candidate(db, principal.tenant.id, candidate_id)
    if candidate.status not in {"pending_review", "approved"}:
        raise _conflict("faq_candidate_not_editable", "当前 FAQ 状态不允许编辑")
    before = {
        "title": candidate.title,
        "category": candidate.category,
        "question": candidate.canonical_question,
        "variants": candidate.question_variants,
        "answer": candidate.answer,
        "effective_date": candidate.effective_date,
    }
    updates = payload.model_dump(exclude_unset=True)
    if any(
        value is None and field != "effective_date"
        for field, value in updates.items()
    ):
        raise HTTPException(
            status_code=422,
            detail={"code": "null_not_allowed", "message": "FAQ 字段不允许设置为 null"},
        )
    for field, value in updates.items():
        if field == "canonical_question":
            candidate.canonical_question = value
        else:
            setattr(candidate, field, value)
    normalized_question = "".join(
        char.casefold()
        for char in candidate.canonical_question
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )
    normalized_answer = "".join(
        char.casefold()
        for char in candidate.answer
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )
    candidate.fingerprint = hashlib.sha256(
        (normalized_question + "\x1f" + normalized_answer).encode("utf-8")
    ).hexdigest()
    duplicate = db.scalar(
        select(FaqCandidate.id).where(
            FaqCandidate.tenant_id == principal.tenant.id,
            FaqCandidate.analysis_job_id == candidate.analysis_job_id,
            FaqCandidate.fingerprint == candidate.fingerprint,
            FaqCandidate.id != candidate.id,
        )
    )
    if duplicate is not None:
        raise _conflict(
            "faq_candidate_duplicate",
            "该问题和答案与同一批次的另一条候选知识重复，请合并后再保存",
        )
    candidate.status = "pending_review"
    candidate.approved_at = None
    after = {
        "title": candidate.title,
        "category": candidate.category,
        "question": candidate.canonical_question,
        "variants": candidate.question_variants,
        "answer": candidate.answer,
        "effective_date": candidate.effective_date,
    }
    _record_event(
        db,
        principal=principal,
        source_id=candidate.source_id,
        entity_type="faq_candidate",
        entity_id=candidate.id,
        action="edited",
        before=before,
        after=after,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _conflict("faq_candidate_duplicate", "该候选知识与同批次的另一条内容重复") from None
    db.refresh(candidate)
    return _faq_candidate_read(candidate)


@router.post("/faq-candidates/{candidate_id}/approve", response_model=FaqCandidateRead)
def approve_faq_candidate(
    candidate_id: UUID,
    payload: ReviewDecision,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> FaqCandidateRead:
    candidate = _get_faq_candidate(db, principal.tenant.id, candidate_id)
    if candidate.status != "pending_review":
        raise _conflict("faq_candidate_not_reviewable", "只有待审核的 FAQ 候选知识可以批准")
    candidate.status = "approved"
    candidate.approved_at = datetime.now(UTC)
    _record_event(
        db,
        principal=principal,
        source_id=candidate.source_id,
        entity_type="faq_candidate",
        entity_id=candidate.id,
        action="approved",
        after={"status": candidate.status},
        note=payload.note,
    )
    db.commit()
    db.refresh(candidate)
    return _faq_candidate_read(candidate)


@router.post("/faq-candidates/{candidate_id}/reject", response_model=FaqCandidateRead)
def reject_faq_candidate(
    candidate_id: UUID,
    payload: ReviewDecision,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> FaqCandidateRead:
    candidate = _get_faq_candidate(db, principal.tenant.id, candidate_id)
    if candidate.status not in {"pending_review", "approved"}:
        raise _conflict("faq_candidate_not_rejectable", "当前 FAQ 状态不允许拒绝")
    candidate.status = "rejected"
    candidate.rejected_at = datetime.now(UTC)
    _record_event(
        db,
        principal=principal,
        source_id=candidate.source_id,
        entity_type="faq_candidate",
        entity_id=candidate.id,
        action="rejected",
        after={"status": candidate.status},
        note=payload.note,
    )
    db.commit()
    db.refresh(candidate)
    return _faq_candidate_read(candidate)


@router.post("/faq-candidates/{candidate_id}/publish", response_model=FaqCandidateRead)
def publish_faq_candidate(
    candidate_id: UUID,
    payload: FaqPublishRequest,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> FaqCandidateRead:
    candidate = _get_faq_candidate(db, principal.tenant.id, candidate_id)
    if candidate.status == "published":
        return _faq_candidate_read(candidate)
    if candidate.status != "approved":
        raise _conflict("faq_candidate_not_approved", "只有审核批准的 FAQ 才能发布到知识库")

    knowledge_base = db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == payload.knowledge_base_id,
            KnowledgeBase.tenant_id == principal.tenant.id,
            KnowledgeBase.enabled.is_(True),
        )
    )
    if knowledge_base is None:
        raise _not_found("knowledge_base_not_found", "目标知识库不存在或已停用")

    markdown = [
        "# " + candidate.title,
        "",
        "## 常见问题",
        candidate.canonical_question,
        "",
        "## 标准答复",
        candidate.answer,
    ]
    if candidate.question_variants:
        markdown.extend(["", "## 相关问法"])
        markdown.extend("- " + item for item in candidate.question_variants[:20])
    if candidate.effective_date:
        markdown.extend(["", "## 适用时间", candidate.effective_date])
    markdown.extend(["", "## 分类", candidate.category])
    body = ("\n".join(markdown).strip() + "\n").encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()

    existing = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == principal.tenant.id,
            KnowledgeDocument.knowledge_base_id == knowledge_base.id,
            KnowledgeDocument.sha256 == digest,
            KnowledgeDocument.status != "deleted",
        )
    )
    if existing is not None:
        candidate.status = "published"
        candidate.published_document_id = existing.id
        candidate.published_at = datetime.now(UTC)
        _record_event(
            db,
            principal=principal,
            source_id=candidate.source_id,
            entity_type="faq_candidate",
            entity_id=candidate.id,
            action="published",
            after={"knowledge_base_id": str(knowledge_base.id), "document_id": str(existing.id)},
        )
        db.commit()
        db.refresh(candidate)
        return _faq_candidate_read(candidate)

    document_id = uuid4()
    object_key = (
        f"tenant/{principal.tenant.id}/knowledge/{knowledge_base.id}/"
        f"{document_id.hex}/{uuid4().hex}.md"
    )
    try:
        put_object(object_key, body, "text/markdown; charset=utf-8")
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={"code": "object_storage_unavailable", "message": "知识库对象存储暂时不可用"},
        ) from None

    document = KnowledgeDocument(
        id=document_id,
        tenant_id=principal.tenant.id,
        knowledge_base_id=knowledge_base.id,
        original_filename=(PurePosixPath(candidate.title[:140]).name or "faq") + ".md",
        source_type="upload",
        source_url=None,
        object_key=object_key,
        mime_type="text/markdown",
        file_size_bytes=len(body),
        sha256=digest,
        status="queued",
        active_index_version=0,
        pending_index_version=1,
    )
    ingestion_job = KnowledgeIngestionJob(
        id=uuid4(),
        tenant_id=principal.tenant.id,
        knowledge_base_id=knowledge_base.id,
        document_id=document.id,
        job_type="ingest",
        status="queued",
        dedupe_key=f"faq-publish:{candidate.id}:{knowledge_base.id}",
        index_version=1,
    )
    try:
        db.add(document)
        # Persist the document before rows that reference it through foreign keys.
        db.flush()
        db.add(ingestion_job)
        candidate.status = "published"
        candidate.published_document_id = document.id
        candidate.published_at = datetime.now(UTC)
        _record_event(
            db,
            principal=principal,
            source_id=candidate.source_id,
            entity_type="faq_candidate",
            entity_id=candidate.id,
            action="published",
            after={
                "knowledge_base_id": str(knowledge_base.id),
                "document_id": str(document.id),
            },
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        from app.knowledge.storage import delete_object

        try:
            delete_object(object_key)
        except Exception:
            pass
        raise _conflict("faq_publish_conflict", "FAQ 发布冲突，请刷新后重试") from None
    db.refresh(candidate)
    return _faq_candidate_read(candidate)
