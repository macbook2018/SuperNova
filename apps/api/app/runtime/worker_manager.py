from __future__ import annotations

import argparse
import socket
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from sqlalchemy import or_, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.bot_runtime import (
    BotInstance,
    BotRuntimeCommand,
    BotRuntimeCredential,
    BotRuntimeEvent,
)
from app.models.catalog import Persona
from app.runtime.provider import DockerRuntimeProvider

logger = structlog.get_logger()
WORKER_ID = f"runtime-{socket.gethostname()}-{uuid4().hex[:8]}"


def _provider() -> DockerRuntimeProvider:
    settings = get_settings()
    return DockerRuntimeProvider(
        image=settings.astrbot_runtime_image,
        runtime_root=settings.runtime_data_root,
        memory_limit=settings.runtime_memory_limit,
        cpu_limit=settings.runtime_cpu_limit,
        pids_limit=settings.runtime_pids_limit,
    )


def claim_command() -> UUID | None:
    settings = get_settings()
    now = datetime.now(UTC)
    with SessionLocal() as db:
        command = db.scalar(
            select(BotRuntimeCommand)
            .where(or_(
                (BotRuntimeCommand.status == "queued") & (BotRuntimeCommand.available_at <= now),
                (BotRuntimeCommand.status == "leased") & (BotRuntimeCommand.lease_expires_at < now),
            ))
            .order_by(BotRuntimeCommand.available_at, BotRuntimeCommand.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if command is None:
            return None
        if command.attempts >= command.max_attempts:
            command.status = "failed"
            command.error_code = "runtime_max_attempts_exceeded"
            command.worker_id = None
            command.lease_expires_at = None
            command.completed_at = now
            db.commit()
            return None
        command.status = "leased"
        command.attempts += 1
        command.worker_id = WORKER_ID
        command.lease_expires_at = now + timedelta(seconds=settings.runtime_worker_lease_seconds)
        db.commit()
        return command.id


def _issue_runtime_config(db, bot: BotInstance, persona: Persona) -> dict:
    from app.runtime.security import issue_runtime_token

    settings = get_settings()
    _revoke_credentials(db, bot)
    credentials = {
        scope: issue_runtime_token(db, bot, scope)
        for scope in ("chat.completions", "runtime.heartbeat", "runtime.events")
    }
    db.flush()
    db.commit()
    return {
        "schema_version": 1,
        "tenant_id": str(bot.tenant_id),
        "bot_instance_id": str(bot.id),
        "persona_id": str(persona.id),
        "runtime_version": bot.runtime_version,
        "config_version": bot.config_version,
        "gateway": {
            "base_url": settings.runtime_gateway_base_url.rstrip("/"),
            "model": persona.slug,
            "credentials": credentials,
        },
        "platforms": [],
        "storage": {"data_dir": "/var/lib/astrbot", "session_retention_days": 0},
    }


def _write_event(db, bot: BotInstance, event_type: str, payload: dict) -> None:
    allowed = {
        "level", "component", "error_code", "retryable", "attempt",
        "duration_ms", "state", "resource_id", "code", "reason_code",
    }
    db.add(BotRuntimeEvent(
        id=uuid4(), tenant_id=bot.tenant_id, bot_instance_id=bot.id,
        event_type=event_type, schema_version=1, platform="system",
        correlation_id=uuid4(), occurred_at=datetime.now(UTC),
        payload_json={key: value for key, value in payload.items() if key in allowed},
    ))


def _revoke_credentials(db, bot: BotInstance) -> None:
    now = datetime.now(UTC)
    credentials = db.scalars(select(BotRuntimeCredential).where(
        BotRuntimeCredential.tenant_id == bot.tenant_id,
        BotRuntimeCredential.bot_instance_id == bot.id,
        BotRuntimeCredential.revoked_at.is_(None),
    )).all()
    for credential in credentials:
        credential.revoked_at = now


def _process(command_id: UUID, provider: DockerRuntimeProvider) -> None:
    with SessionLocal() as db:
        command = db.scalar(select(BotRuntimeCommand).where(
            BotRuntimeCommand.id == command_id,
            BotRuntimeCommand.worker_id == WORKER_ID,
            BotRuntimeCommand.status == "leased",
        ))
        if command is None:
            return
        bot = db.scalar(select(BotInstance).where(
            BotInstance.id == command.bot_instance_id,
            BotInstance.tenant_id == command.tenant_id,
        ))
        if bot is None:
            command.status = "failed"
            command.error_code = "runtime_bot_not_found"
            command.completed_at = datetime.now(UTC)
            db.commit()
            return

        action = command.command
        try:
            if action in {"start", "restart", "update_config"}:
                persona = db.scalar(select(Persona).where(
                    Persona.id == bot.persona_id, Persona.tenant_id == bot.tenant_id
                ))
                if persona is None or not persona.enabled:
                    raise RuntimeError("runtime_persona_unavailable")
                bot.actual_state = "provisioning"
                db.commit()
                config = _issue_runtime_config(db, bot, persona)
                resource_id = provider.provision(
                    tenant_id=bot.tenant_id, bot_instance_id=bot.id, runtime_config=config
                )
                bot.runtime_resource_id = resource_id
                bot.actual_state = "starting"
                db.commit()
                if action == "update_config" and bot.desired_state == "stopped":
                    bot.actual_state = "stopped"
                    _write_event(db, bot, "runtime.config_updated", {
                        "state": "stopped", "resource_id": resource_id, "component": "worker"
                    })
                else:
                    if action == "restart" and provider.inspect(resource_id)["running"]:
                        provider.restart(resource_id)
                    else:
                        provider.start(resource_id)
                    _write_event(db, bot, "runtime.state_changed", {
                        "state": "starting", "resource_id": resource_id, "component": "worker"
                    })
            elif action == "stop":
                bot.actual_state = "stopping"
                db.commit()
                if bot.runtime_resource_id:
                    provider.stop(bot.runtime_resource_id)
                _revoke_credentials(db, bot)
                bot.actual_state = "stopped"
                _write_event(db, bot, "runtime.state_changed", {"state": "stopped"})
            elif action == "delete":
                if bot.runtime_resource_id:
                    provider.delete(bot.runtime_resource_id, delete_volumes=True)
                _revoke_credentials(db, bot)
                command.status = "succeeded"
                command.completed_at = datetime.now(UTC)
                _write_event(db, bot, "runtime.deleted", {"state": "deleted"})
                db.delete(bot)
                db.commit()
                return
            else:
                raise RuntimeError("runtime_command_unsupported")

            command.status = "succeeded"
            command.completed_at = datetime.now(UTC)
            command.lease_expires_at = None
            command.worker_id = None
            db.commit()
        except Exception as exc:
            db.rollback()
            current = db.scalar(select(BotRuntimeCommand).where(
                BotRuntimeCommand.id == command_id, BotRuntimeCommand.worker_id == WORKER_ID
            ))
            if current is None:
                return
            now = datetime.now(UTC)
            current.error_code = (
                str(exc).splitlines()[0][:100]
                if isinstance(exc, (ValueError, RuntimeError))
                else exc.__class__.__name__
            )
            current.worker_id = None
            current.lease_expires_at = None
            if current.attempts >= current.max_attempts:
                current.status = "failed"
                current.completed_at = now
                failed_bot = db.scalar(select(BotInstance).where(
                    BotInstance.id == current.bot_instance_id,
                    BotInstance.tenant_id == current.tenant_id,
                ))
                if failed_bot is not None:
                    failed_bot.actual_state = "failed"
                    failed_bot.last_error_code = current.error_code
                    _write_event(db, failed_bot, "runtime.error", {
                        "level": "error", "component": "worker",
                        "error_code": current.error_code,
                    })
            else:
                current.status = "queued"
                current.available_at = now + timedelta(
                    seconds=min(300, 2 ** min(current.attempts, 8))
                )
            db.commit()
            logger.warning(
                "runtime_command_failed", command_id=str(command_id),
                attempts=current.attempts, error_code=current.error_code,
            )


def run_forever() -> None:
    settings = get_settings()
    provider = _provider()
    logger.info("runtime_worker_manager_started", worker_id=WORKER_ID)
    while True:
        command_id = claim_command()
        if command_id is None:
            time.sleep(settings.runtime_worker_poll_seconds)
            continue
        _process(command_id, provider)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        with SessionLocal() as db:
            db.execute(select(1))
        _provider().client.ping()
        print("runtime worker manager database and Docker checks passed")
        return
    run_forever()


if __name__ == "__main__":
    main()
