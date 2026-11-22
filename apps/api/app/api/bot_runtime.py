from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query, status
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, DatabaseSession, Principal
from app.core.config import get_settings
from app.models.bot_runtime import (
    BotInstance,
    BotRuntimeCommand,
    BotRuntimeCredential,
    BotRuntimeEvent,
)
from app.models.catalog import Persona
from app.schemas.bot_runtime import (
    BotInstanceCreate,
    BotInstanceRead,
    BotInstanceUpdate,
    BotRuntimeCommandRead,
    BotRuntimeEventRead,
    BotRuntimeLogsRead,
    LifecycleCommandResponse,
)

router = APIRouter(tags=["机器人实例"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "bot_instance_not_found", "message": "机器人不存在"},
    )


def _get_bot(db: Session, principal: Principal, bot_id: UUID) -> BotInstance:
    bot = db.scalar(
        select(BotInstance).where(
            BotInstance.id == bot_id,
            BotInstance.tenant_id == principal.tenant.id,
        )
    )
    if bot is None:
        raise _not_found()
    return bot


def _enqueue_command(
    db: Session,
    bot: BotInstance,
    command: str,
    idempotency_key: str | None,
    *,
    dedupe_pending: bool = True,
) -> BotRuntimeCommand:
    key = idempotency_key.strip() if idempotency_key else uuid4().hex
    if (
        not key
        or len(key) > 128
        or any(ord(char) < 33 or ord(char) > 126 for char in key)
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_idempotency_key",
                "message": "Idempotency-Key 必须为 1 至 128 个可见 ASCII 字符",
            },
        )

    existing = db.scalar(
        select(BotRuntimeCommand).where(
            BotRuntimeCommand.tenant_id == bot.tenant_id,
            BotRuntimeCommand.idempotency_key == key,
        )
    )
    if existing is not None:
        if existing.bot_instance_id != bot.id or existing.command != command:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "runtime_idempotency_conflict",
                    "message": "同一个 Idempotency-Key 不能用于不同机器人或不同命令",
                },
            )
        return existing

    if dedupe_pending:
        pending = db.scalar(
            select(BotRuntimeCommand)
            .where(
                BotRuntimeCommand.tenant_id == bot.tenant_id,
                BotRuntimeCommand.bot_instance_id == bot.id,
                BotRuntimeCommand.command == command,
                BotRuntimeCommand.status.in_(("queued", "leased")),
            )
            .order_by(BotRuntimeCommand.created_at.desc())
            .limit(1)
        )
        if pending is not None:
            return pending

    item = BotRuntimeCommand(
        id=uuid4(),
        tenant_id=bot.tenant_id,
        bot_instance_id=bot.id,
        command=command,
        idempotency_key=key,
        status="queued",
        attempts=0,
        max_attempts=5,
    )
    db.add(item)
    db.flush()
    return item


def _command_response(
    bot: BotInstance,
    command: BotRuntimeCommand | None,
    *,
    already_satisfied: bool = False,
) -> LifecycleCommandResponse:
    return LifecycleCommandResponse(
        bot_instance_id=bot.id,
        desired_state=bot.desired_state,
        command=BotRuntimeCommandRead.model_validate(command) if command else None,
        already_satisfied=already_satisfied,
    )


@router.get("/bot-instances", response_model=list[BotInstanceRead])
def list_bot_instances(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[BotInstance]:
    return list(
        db.scalars(
            select(BotInstance)
            .where(BotInstance.tenant_id == principal.tenant.id)
            .order_by(BotInstance.created_at.desc())
        ).all()
    )


@router.post(
    "/bot-instances",
    response_model=BotInstanceRead,
    status_code=status.HTTP_201_CREATED,
)
def create_bot_instance(
    payload: BotInstanceCreate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> BotInstance:
    persona = db.scalar(
        select(Persona).where(
            Persona.id == payload.persona_id,
            Persona.tenant_id == principal.tenant.id,
        )
    )
    if persona is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "persona_not_found", "message": "指定的人格不存在"},
        )
    if not persona.enabled:
        raise HTTPException(
            status_code=409,
            detail={"code": "persona_disabled", "message": "已停用的人格不能绑定到新机器人"},
        )

    item = BotInstance(
        id=uuid4(),
        tenant_id=principal.tenant.id,
        persona_id=persona.id,
        created_by_user_id=principal.user.id,
        name=payload.name,
        runtime_version=get_settings().astrbot_runtime_version,
        desired_state="stopped",
        actual_state="pending",
        config_version=1,
        runtime_provider="docker",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/bot-instances/{bot_id}", response_model=BotInstanceRead)
def get_bot_instance(
    bot_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> BotInstance:
    return _get_bot(db, principal, bot_id)


@router.patch("/bot-instances/{bot_id}", response_model=BotInstanceRead)
def update_bot_instance(
    bot_id: UUID,
    payload: BotInstanceUpdate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> BotInstance:
    bot = _get_bot(db, principal, bot_id)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=422,
            detail={"code": "empty_update", "message": "至少需要提供一个可更新字段"},
        )
    if bot.desired_state != "stopped" or bot.actual_state not in {
        "pending", "stopped", "failed"
    }:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "bot_runtime_busy",
                "message": "只有停止状态的机器人才能修改名称或绑定人格",
            },
        )

    persona_changed = False
    if "persona_id" in updates:
        persona = db.scalar(
            select(Persona).where(
                Persona.id == updates["persona_id"],
                Persona.tenant_id == principal.tenant.id,
            )
        )
        if persona is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "persona_not_found", "message": "指定的人格不存在"},
            )
        if not persona.enabled:
            raise HTTPException(
                status_code=409,
                detail={"code": "persona_disabled", "message": "已停用的人格不能绑定到机器人"},
            )
        persona_changed = persona.id != bot.persona_id

    if persona_changed:
        db.execute(
            delete(BotRuntimeCredential).where(
                BotRuntimeCredential.tenant_id == bot.tenant_id,
                BotRuntimeCredential.bot_instance_id == bot.id,
            )
        )
        bot.persona_id = updates["persona_id"]

    if "name" in updates:
        bot.name = updates["name"]
    bot.config_version += 1

    if bot.runtime_resource_id:
        _enqueue_command(db, bot, "update_config", None)

    db.commit()
    db.refresh(bot)
    return bot


@router.delete(
    "/bot-instances/{bot_id}",
    response_model=LifecycleCommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def delete_bot_instance(
    bot_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LifecycleCommandResponse:
    bot = _get_bot(db, principal, bot_id)
    if bot.desired_state == "deleting":
        command = db.scalar(
            select(BotRuntimeCommand)
            .where(
                BotRuntimeCommand.tenant_id == bot.tenant_id,
                BotRuntimeCommand.bot_instance_id == bot.id,
                BotRuntimeCommand.command == "delete",
                BotRuntimeCommand.status.in_(("queued", "leased")),
            )
            .order_by(BotRuntimeCommand.created_at.desc())
            .limit(1)
        )
        return _command_response(bot, command)
    bot.desired_state = "deleting"
    command = _enqueue_command(db, bot, "delete", idempotency_key)
    db.commit()
    db.refresh(bot)
    db.refresh(command)
    return _command_response(bot, command)


def _lifecycle_command(
    bot_id: UUID,
    principal: Principal,
    db: Session,
    command_name: str,
    desired_state: str,
    idempotency_key: str | None,
) -> LifecycleCommandResponse:
    bot = _get_bot(db, principal, bot_id)
    if bot.desired_state == "deleting":
        raise HTTPException(
            status_code=409,
            detail={"code": "bot_deleting", "message": "正在删除的机器人不能执行生命周期命令"},
        )

    if command_name == "start" and bot.actual_state == "running" and bot.desired_state == "running":
        return _command_response(bot, None, already_satisfied=True)
    if (
        command_name == "stop"
        and bot.actual_state in {"pending", "stopped"}
        and bot.desired_state == "stopped"
        and not bot.runtime_resource_id
    ):
        return _command_response(bot, None, already_satisfied=True)

    bot.desired_state = desired_state
    command = _enqueue_command(
        db,
        bot,
        command_name,
        idempotency_key,
        dedupe_pending=command_name != "restart",
    )
    db.commit()
    db.refresh(bot)
    db.refresh(command)
    return _command_response(bot, command)


@router.post(
    "/bot-instances/{bot_id}/start",
    response_model=LifecycleCommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_bot_instance(
    bot_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LifecycleCommandResponse:
    return _lifecycle_command(bot_id, principal, db, "start", "running", idempotency_key)


@router.post(
    "/bot-instances/{bot_id}/stop",
    response_model=LifecycleCommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def stop_bot_instance(
    bot_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LifecycleCommandResponse:
    return _lifecycle_command(bot_id, principal, db, "stop", "stopped", idempotency_key)


@router.post(
    "/bot-instances/{bot_id}/restart",
    response_model=LifecycleCommandResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def restart_bot_instance(
    bot_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LifecycleCommandResponse:
    return _lifecycle_command(bot_id, principal, db, "restart", "running", idempotency_key)


@router.get("/bot-instances/{bot_id}/status", response_model=BotInstanceRead)
def get_bot_instance_status(
    bot_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> BotInstance:
    return _get_bot(db, principal, bot_id)


@router.get("/bot-instances/{bot_id}/logs", response_model=BotRuntimeLogsRead)
def get_bot_instance_logs(
    bot_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    cursor: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> BotRuntimeLogsRead:
    bot = _get_bot(db, principal, bot_id)
    statement = select(BotRuntimeEvent).where(
        BotRuntimeEvent.tenant_id == principal.tenant.id,
        BotRuntimeEvent.bot_instance_id == bot.id,
        BotRuntimeEvent.event_type.in_(("runtime.log", "runtime.error")),
    )
    if cursor is not None:
        cursor_event = db.scalar(
            select(BotRuntimeEvent).where(
                BotRuntimeEvent.id == cursor,
                BotRuntimeEvent.tenant_id == principal.tenant.id,
                BotRuntimeEvent.bot_instance_id == bot.id,
            )
        )
        if cursor_event is None:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_log_cursor", "message": "日志游标无效"},
            )
        statement = statement.where(
            or_(
                BotRuntimeEvent.created_at < cursor_event.created_at,
                and_(
                    BotRuntimeEvent.created_at == cursor_event.created_at,
                    BotRuntimeEvent.id < cursor_event.id,
                ),
            )
        )

    events = list(
        db.scalars(
            statement.order_by(BotRuntimeEvent.created_at.desc(), BotRuntimeEvent.id.desc())
            .limit(limit)
        ).all()
    )
    next_cursor = events[-1].id if len(events) == limit else None
    return BotRuntimeLogsRead(
        items=[BotRuntimeEventRead.model_validate(event) for event in events],
        next_cursor=next_cursor,
    )
