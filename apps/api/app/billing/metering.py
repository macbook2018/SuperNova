"""Idempotent quota reservation, usage rating and shadow-ledger services.

This module deliberately writes shadow accounting only. It never debits a wallet,
changes a cash balance, or enables real overage collection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.billing.entitlements import (
    _effective_limit,
    _entitlement_map,
    _period_usage,
)
from app.models.billing import (
    BillingAuditEvent,
    BillingLedgerAccount,
    BillingLedgerPosting,
    BillingLedgerTransaction,
    BillingPlan,
    BillingPlanEntitlement,
    BillingRatedItem,
    BillingReconciliationRun,
    BillingReservation,
    BillingReservationItem,
    BillingUsageEvent,
    PlatformModelPrice,
    TenantBillingPolicy,
    TenantSubscription,
)
from app.models.identity import Tenant
from app.models.usage import UsageRecord


@dataclass(frozen=True)
class ReservationDecision:
    reservation_id: UUID
    replay_response: dict | None = None
    replay_status: int | None = None


def estimate_input_token_upper_bound(messages: list[dict]) -> int:
    """Use UTF-8 byte length as a conservative tokenizer-independent upper bound."""
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    return max(1, len(serialized.encode("utf-8")) + 8 * len(messages))


def fingerprint_gateway_request(
    upstream_payload: dict,
    *,
    api_key_id: UUID,
    persona_id: UUID,
    model_source: str,
    price_version_id: UUID | None,
) -> str:
    canonical = {
        "payload": upstream_payload,
        "api_key_id": str(api_key_id),
        "persona_id": str(persona_id),
        "model_source": model_source,
        "price_version_id": str(price_version_id) if price_version_id else None,
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _current_subscription_for_update(
    db: Session,
    tenant_id: UUID,
    now: datetime,
) -> tuple[TenantSubscription, BillingPlan] | None:
    row = db.execute(
        select(TenantSubscription, BillingPlan)
        .join(BillingPlan, BillingPlan.id == TenantSubscription.plan_id)
        .where(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.status.in_(("active", "trialing")),
            TenantSubscription.current_period_start <= now,
            TenantSubscription.current_period_end > now,
        )
        .order_by(TenantSubscription.created_at.desc())
        .with_for_update(of=TenantSubscription)
    ).first()
    return (row[0], row[1]) if row else None


def _held_units(
    db: Session,
    *,
    tenant_id: UUID,
    subscription_id: UUID,
    period_start: datetime,
    period_end: datetime,
    resource_code: str,
) -> int:
    value = db.scalar(
        select(func.coalesce(func.sum(BillingReservationItem.reserved_units), 0))
        .join(
            BillingReservation,
            BillingReservation.id == BillingReservationItem.reservation_id,
        )
        .where(
            BillingReservation.tenant_id == tenant_id,
            BillingReservation.subscription_id == subscription_id,
            BillingReservation.period_start == period_start,
            BillingReservation.period_end == period_end,
            BillingReservation.state.in_(("held", "unknown")),
            BillingReservationItem.resource_code == resource_code,
            BillingReservationItem.state.in_(("held", "unknown")),
        )
    )
    return int(value or 0)


def _currency_and_price(
    db: Session,
    *,
    policy: TenantBillingPolicy | None,
    price_version_id: UUID | None,
) -> tuple[str, PlatformModelPrice | None]:
    currency = policy.currency if policy is not None else "CNY"
    price = db.get(PlatformModelPrice, price_version_id) if price_version_id else None
    if price is not None:
        currency = price.currency
    return currency, price


def _estimate_max_amount_minor(
    price: PlatformModelPrice | None,
    input_tokens: int,
    output_tokens: int,
) -> int:
    if price is None:
        return 0
    amount = (
        Decimal(input_tokens) * price.input_per_million_minor
        + Decimal(output_tokens) * price.output_per_million_minor
    ) / Decimal(1_000_000)
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def _existing_decision(
    reservation: BillingReservation,
    request_hash: str,
) -> ReservationDecision:
    if reservation.request_hash and reservation.request_hash != request_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_key_conflict",
                "message": "同一个 Idempotency-Key 不能用于不同的请求内容",
            },
        )
    if reservation.response_status == 200 and reservation.response_json is not None:
        return ReservationDecision(
            reservation_id=reservation.id,
            replay_response=reservation.response_json,
            replay_status=reservation.response_status,
        )
    if reservation.state == "unknown":
        code = "idempotency_outcome_unknown"
        message = "该请求结果尚待用量核对，系统不会重复调用模型供应商"
    elif reservation.state == "held":
        code = "idempotency_in_progress"
        message = "相同请求仍在处理中，请稍后查询，不要重复提交"
    else:
        code = "idempotency_key_replayed"
        message = "该幂等请求已经处理；如需发起新请求，请使用新的 Idempotency-Key"
    raise HTTPException(status_code=409, detail={"code": code, "message": message})


def find_existing_gateway_request(
    db: Session,
    *,
    tenant_id: UUID,
    idempotency_key: str,
    request_hash: str,
) -> ReservationDecision | None:
    reservation = db.scalar(
        select(BillingReservation).where(
            BillingReservation.tenant_id == tenant_id,
            BillingReservation.event_key == idempotency_key,
        )
    )
    if reservation is None:
        return None
    return _existing_decision(reservation, request_hash)


def reserve_gateway_request(
    db: Session,
    *,
    tenant_id: UUID,
    idempotency_key: str,
    request_hash: str,
    model_source: str,
    input_token_reservation: int,
    output_token_reservation: int,
    price_version_id: UUID | None,
) -> ReservationDecision:
    """Atomically reserve request/token quotas and claim an idempotency key.

    PostgreSQL serializes reservations for one tenant by locking the tenant row.
    Unique constraints remain the final race guard. SQLite ignores FOR UPDATE,
    so concurrency guarantees are verified against PostgreSQL in integration CI.
    """
    now = datetime.now(UTC)
    tenant = db.scalar(
        select(Tenant).where(Tenant.id == tenant_id).with_for_update()
    )
    if tenant is None:
        raise HTTPException(status_code=404, detail="工作空间不存在")

    existing = db.scalar(
        select(BillingReservation)
        .where(
            BillingReservation.tenant_id == tenant_id,
            BillingReservation.event_key == idempotency_key,
        )
        .with_for_update()
    )
    if existing is not None:
        return _existing_decision(existing, request_hash)

    policy = db.scalar(
        select(TenantBillingPolicy)
        .where(TenantBillingPolicy.tenant_id == tenant_id)
        .with_for_update()
    )
    current = _current_subscription_for_update(db, tenant_id, now)
    legacy_byok = policy is None and model_source == "byok"
    if current is None and not legacy_byok:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "subscription_required",
                "message": "当前工作空间没有有效订阅，请先开通或联系平台管理员分配套餐",
            },
        )

    subscription = None
    plan = None
    entitlements: dict[str, BillingPlanEntitlement] = {}
    if current is not None:
        subscription, plan = current
        if plan.status != "active":
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "subscription_plan_unavailable",
                    "message": "当前订阅套餐不可用，请联系平台管理员",
                },
            )
        entitlements = _entitlement_map(db, plan.id)

    requested: dict[str, int] = {"api_requests": 1}
    if model_source == "platform":
        requested.update(
            {
                "platform_model_requests": 1,
                "platform_input_tokens": max(1, input_token_reservation),
                "platform_output_tokens": max(1, output_token_reservation),
            }
        )

    if subscription is not None and plan is not None:
        for resource_code, units in requested.items():
            entitlement = entitlements.get(resource_code)
            if entitlement is None:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "entitlement_not_configured",
                        "message": f"当前套餐未配置所需资源权益：{resource_code}",
                    },
                )
            used = _period_usage(
                db,
                tenant_id,
                subscription.current_period_start,
                subscription.current_period_end,
                resource_code,
            )
            held = _held_units(
                db,
                tenant_id=tenant_id,
                subscription_id=subscription.id,
                period_start=subscription.current_period_start,
                period_end=subscription.current_period_end,
                resource_code=resource_code,
            )
            limit = _effective_limit(entitlement)
            if used + held + units > limit:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": "quota_exceeded",
                        "message": (
                            f"套餐资源额度不足：{resource_code}。"
                            "请求尚未发送到模型供应商，未产生模型 Token 费用"
                        ),
                    },
                )

    _, price = _currency_and_price(
        db,
        policy=policy,
        price_version_id=price_version_id,
    )
    reservation = BillingReservation(
        id=uuid4(),
        tenant_id=tenant_id,
        event_key=idempotency_key,
        request_hash=request_hash,
        model_source=model_source,
        subscription_id=subscription.id if subscription else None,
        plan_id=plan.id if plan else None,
        price_version_id=price.id if price else None,
        period_start=subscription.current_period_start if subscription else None,
        period_end=subscription.current_period_end if subscription else None,
        max_amount_minor=_estimate_max_amount_minor(
            price,
            input_token_reservation,
            output_token_reservation,
        ),
        state="held",
        expires_at=now + timedelta(minutes=5),
    )
    db.add(reservation)
    db.flush()
    db.add_all(
        [
            BillingReservationItem(
                id=uuid4(),
                reservation_id=reservation.id,
                resource_code=resource_code,
                reserved_units=units,
                consumed_units=0,
                state="held",
            )
            for resource_code, units in requested.items()
            if subscription is not None
        ]
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(BillingReservation).where(
                BillingReservation.tenant_id == tenant_id,
                BillingReservation.event_key == idempotency_key,
            )
        )
        if existing is not None:
            return _existing_decision(existing, request_hash)
        raise
    return ReservationDecision(reservation_id=reservation.id)


def _record_event(
    db: Session,
    *,
    reservation: BillingReservation,
    usage_record: UsageRecord,
    resource_code: str,
    quantity: int,
    currency: str,
    plan_id: UUID | None,
    price: PlatformModelPrice | None = None,
    unit_price: Decimal | None = None,
    state: str,
    included_units: int = 0,
    overage_units: int = 0,
    amount_minor: Decimal = Decimal("0"),
) -> BillingRatedItem:
    event_key = f"{reservation.event_key}:{resource_code}"
    event = db.scalar(
        select(BillingUsageEvent)
        .where(
            BillingUsageEvent.tenant_id == reservation.tenant_id,
            BillingUsageEvent.event_key == event_key,
        )
        .with_for_update()
    )
    if event is None:
        event = BillingUsageEvent(
            id=uuid4(),
            tenant_id=reservation.tenant_id,
            source_usage_record_id=usage_record.id,
            reservation_id=reservation.id,
            event_key=event_key,
            resource_code=resource_code,
            model_source=reservation.model_source or "byok",
            quantity_units=quantity,
            status="observed",
            occurred_at=usage_record.created_at or datetime.now(UTC),
        )
        db.add(event)
        db.flush()
    else:
        event.source_usage_record_id = usage_record.id
        event.reservation_id = reservation.id
        event.quantity_units = quantity
        event.status = "observed"

    rated = db.scalar(
        select(BillingRatedItem)
        .where(BillingRatedItem.usage_event_id == event.id)
        .with_for_update()
    )
    if rated is None:
        rated = BillingRatedItem(
            id=uuid4(),
            usage_event_id=event.id,
            plan_id=plan_id,
            price_version_id=price.id if price else None,
            included_units=included_units,
            overage_units=overage_units,
            amount_minor_decimal=amount_minor,
            unit_price_minor_per_unit=unit_price,
            currency=currency,
            state=state,
            rated_at=datetime.now(UTC),
        )
        db.add(rated)
    else:
        expected = (
            plan_id,
            price.id if price else None,
            included_units,
            overage_units,
            amount_minor,
            unit_price,
            currency,
            state,
        )
        existing = (
            rated.plan_id,
            rated.price_version_id,
            rated.included_units,
            rated.overage_units,
            rated.amount_minor_decimal,
            rated.unit_price_minor_per_unit,
            rated.currency,
            rated.state,
        )
        if existing != expected:
            raise RuntimeError("an immutable rated item cannot be changed after posting")
    db.flush()
    if state == "shadow" and amount_minor > 0:
        _write_shadow_ledger(
            db,
            tenant_id=reservation.tenant_id,
            rated_item=rated,
            usage_event=event,
            amount_minor=amount_minor,
            currency=currency,
        )
    return rated


def _get_or_create_shadow_account(
    db: Session,
    *,
    tenant_id: UUID,
    currency: str,
    kind: str,
) -> BillingLedgerAccount:
    account_key = f"tenant:{tenant_id}:shadow:{kind}:{currency}"
    account = db.scalar(
        select(BillingLedgerAccount)
        .where(BillingLedgerAccount.account_key == account_key)
        .with_for_update()
    )
    if account is None:
        account = BillingLedgerAccount(
            id=uuid4(),
            account_key=account_key,
            tenant_id=tenant_id,
            currency=currency,
            kind=kind,
        )
        db.add(account)
        db.flush()
    if account.currency != currency:
        raise RuntimeError("shadow ledger account currency mismatch")
    return account


def _write_shadow_ledger(
    db: Session,
    *,
    tenant_id: UUID,
    rated_item: BillingRatedItem,
    usage_event: BillingUsageEvent,
    amount_minor: Decimal,
    currency: str,
) -> None:
    rounded = int(amount_minor.to_integral_value(rounding=ROUND_HALF_UP))
    if rounded <= 0:
        return
    idempotency_key = f"shadow-rated-item:{rated_item.id}"
    existing = db.scalar(
        select(BillingLedgerTransaction).where(
            BillingLedgerTransaction.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        return

    tenant_usage = _get_or_create_shadow_account(
        db,
        tenant_id=tenant_id,
        currency=currency,
        kind="shadow_usage",
    )
    platform_revenue = _get_or_create_shadow_account(
        db,
        tenant_id=tenant_id,
        currency=currency,
        kind="shadow_platform_revenue",
    )
    transaction = BillingLedgerTransaction(
        id=uuid4(),
        idempotency_key=idempotency_key,
        kind="shadow_usage",
        mode="shadow",
        currency=currency,
        reference_id=str(usage_event.id),
        metadata_json={
            "tenant_id": str(tenant_id),
            "rated_item_id": str(rated_item.id),
            "resource_code": usage_event.resource_code,
            "model_source": usage_event.model_source,
            "price_version_id": str(rated_item.price_version_id)
            if rated_item.price_version_id
            else None,
        },
    )
    db.add(transaction)
    db.flush()
    postings = [
        BillingLedgerPosting(
            id=uuid4(),
            transaction_id=transaction.id,
            account_id=tenant_usage.id,
            amount_minor=rounded,
        ),
        BillingLedgerPosting(
            id=uuid4(),
            transaction_id=transaction.id,
            account_id=platform_revenue.id,
            amount_minor=-rounded,
        ),
    ]
    if sum(item.amount_minor for item in postings) != 0:
        raise RuntimeError("shadow ledger transaction is not balanced")
    db.add_all(postings)
    db.flush()


def finalize_gateway_request(
    db: Session,
    *,
    reservation_id: UUID,
    usage_record: UsageRecord,
    trusted_usage: dict[str, int] | None,
    usage_unknown: bool,
    response_status: int | None = None,
    response_json: dict | None = None,
) -> None:
    reservation_ref = db.scalar(
        select(BillingReservation).where(BillingReservation.id == reservation_id)
    )
    if reservation_ref is None:
        raise RuntimeError("billing reservation not found")
    # Keep lock order consistent with reserve_gateway_request: tenant first,
    # then reservation. This also serializes creation of per-tenant ledger accounts.
    db.scalar(
        select(Tenant)
        .where(Tenant.id == reservation_ref.tenant_id)
        .with_for_update()
    )
    reservation = db.scalar(
        select(BillingReservation)
        .where(BillingReservation.id == reservation_id)
        .with_for_update()
    )
    if reservation is None:
        raise RuntimeError("billing reservation not found")
    if reservation.state == "settled":
        return
    was_unknown = reservation.state == "unknown"
    was_released = reservation.state == "released"

    db.flush()
    policy = db.get(TenantBillingPolicy, reservation.tenant_id)
    currency, price = _currency_and_price(
        db,
        policy=policy,
        price_version_id=reservation.price_version_id,
    )
    plan_id = reservation.plan_id

    _record_event(
        db,
        reservation=reservation,
        usage_record=usage_record,
        resource_code="api_requests",
        quantity=1,
        currency=currency,
        plan_id=plan_id,
        state="included" if plan_id else "unmetered",
        included_units=1 if plan_id else 0,
    )
    if reservation.model_source == "platform":
        _record_event(
            db,
            reservation=reservation,
            usage_record=usage_record,
            resource_code="platform_model_requests",
            quantity=1,
            currency=currency,
            plan_id=plan_id,
            state="included",
            included_units=1,
        )

    item_by_code = {
        item.resource_code: item
        for item in db.scalars(
            select(BillingReservationItem).where(
                BillingReservationItem.reservation_id == reservation.id
            )
        ).all()
    }

    if trusted_usage is not None:
        prompt_tokens = trusted_usage["prompt_tokens"]
        completion_tokens = trusted_usage["completion_tokens"]
        if reservation.model_source == "platform":
            for resource_code, quantity, price_field in [
                ("platform_input_tokens", prompt_tokens, "input_per_million_minor"),
                ("platform_output_tokens", completion_tokens, "output_per_million_minor"),
            ]:
                reserved_item = item_by_code.get(resource_code)
                reserved_units = reserved_item.reserved_units if reserved_item else 0
                included = min(quantity, reserved_units)
                overage = max(0, quantity - included)
                if price is None:
                    state = "unrated"
                    unit_price = None
                    amount = Decimal("0")
                else:
                    rate = getattr(price, price_field)
                    unit_price = rate / Decimal(1_000_000)
                    amount = (Decimal(quantity) * unit_price).quantize(
                        Decimal("0.00000001"),
                        rounding=ROUND_HALF_UP,
                    )
                    state = "shadow"
                _record_event(
                    db,
                    reservation=reservation,
                    usage_record=usage_record,
                    resource_code=resource_code,
                    quantity=quantity,
                    currency=currency,
                    plan_id=plan_id,
                    price=price,
                    unit_price=unit_price,
                    state=state,
                    included_units=included,
                    overage_units=overage,
                    amount_minor=amount,
                )
                if overage:
                    db.add(
                        BillingAuditEvent(
                            id=uuid4(),
                            tenant_id=reservation.tenant_id,
                            action="reservation_overrun",
                            subject_type="billing_reservation",
                            subject_id=str(reservation.id),
                            reason="上游返回的可信 Token 用量超过预留额度",
                            metadata_json={
                                "resource_code": resource_code,
                                "reserved_units": reserved_units,
                                "actual_units": quantity,
                            },
                        )
                    )
        else:
            for resource_code, quantity in [
                ("byok_input_tokens", prompt_tokens),
                ("byok_output_tokens", completion_tokens),
            ]:
                _record_event(
                    db,
                    reservation=reservation,
                    usage_record=usage_record,
                    resource_code=resource_code,
                    quantity=quantity,
                    currency=currency,
                    plan_id=plan_id,
                    state="unmetered",
                )

    unknown_items = []
    for resource_code, item in item_by_code.items():
        if resource_code == "api_requests":
            item.consumed_units = min(1, item.reserved_units)
            item.state = "settled"
            continue
        if resource_code == "platform_model_requests":
            item.consumed_units = min(1, item.reserved_units)
            item.state = "settled"
            continue
        if trusted_usage is not None:
            actual = (
                trusted_usage["prompt_tokens"]
                if resource_code == "platform_input_tokens"
                else trusted_usage["completion_tokens"]
            )
            item.consumed_units = min(actual, item.reserved_units)
            item.state = "settled"
        elif usage_unknown and not was_released:
            item.state = "unknown"
            unknown_items.append(item)
        elif was_released:
            item.consumed_units = 0
            item.state = "released"
        else:
            item.consumed_units = 0
            item.state = "released"

    reservation.usage_record_id = usage_record.id
    reservation.response_status = response_status
    reservation.response_json = response_json
    reservation.state = (
        "unknown"
        if unknown_items
        else "released"
        if was_released and trusted_usage is None
        else "settled"
    )
    reservation.settled_at = None if unknown_items else datetime.now(UTC)
    if was_released and trusted_usage is not None:
        db.add(
            BillingAuditEvent(
                id=uuid4(),
                tenant_id=reservation.tenant_id,
                action="late_usage_after_release",
                subject_type="billing_reservation",
                subject_id=str(reservation.id),
                reason="管理员释放预留后仍收到可信上游 usage，已记录实际影子用量",
                metadata_json={"request_key": reservation.event_key},
            )
        )
    if was_unknown and trusted_usage is not None:
        db.add(
            BillingAuditEvent(
                id=uuid4(),
                tenant_id=reservation.tenant_id,
                action="usage_outcome_resolved",
                subject_type="billing_reservation",
                subject_id=str(reservation.id),
                reason="补获可信 Token usage，完成未知请求的影子计价",
                metadata_json={"request_key": reservation.event_key},
            )
        )
    if unknown_items:
        db.add(
            BillingAuditEvent(
                id=uuid4(),
                tenant_id=reservation.tenant_id,
                action="usage_outcome_unknown",
                subject_type="billing_reservation",
                subject_id=str(reservation.id),
                reason="模型调用结果没有可信的完整 Token usage，保留额度等待对账",
                metadata_json={
                    "request_key": reservation.event_key,
                    "unknown_resources": [item.resource_code for item in unknown_items],
                },
            )
        )
    db.commit()


def run_billing_reconciliation(
    db: Session,
    *,
    actor_user_id: UUID,
    period_start: datetime,
    period_end: datetime,
) -> BillingReconciliationRun:
    if period_start.tzinfo is None or period_end.tzinfo is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "timezone_required", "message": "对账周期必须包含时区"},
        )
    if period_end <= period_start:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_reconciliation_period",
                "message": "对账结束时间必须晚于开始时间",
            },
        )

    transaction_rows = db.execute(
        select(
            BillingLedgerTransaction.id,
            BillingLedgerTransaction.currency,
            func.coalesce(func.sum(BillingLedgerPosting.amount_minor), 0),
            func.count(BillingLedgerPosting.id),
        )
        .outerjoin(
            BillingLedgerPosting,
            BillingLedgerPosting.transaction_id == BillingLedgerTransaction.id,
        )
        .where(
            BillingLedgerTransaction.mode == "shadow",
            BillingLedgerTransaction.created_at >= period_start,
            BillingLedgerTransaction.created_at < period_end,
        )
        .group_by(BillingLedgerTransaction.id, BillingLedgerTransaction.currency)
    ).all()
    unbalanced = [
        {
            "transaction_id": str(row[0]),
            "currency": row[1],
            "net_amount_minor": int(row[2]),
            "posting_count": int(row[3]),
        }
        for row in transaction_rows
        if int(row[2]) != 0 or int(row[3]) < 2
    ]

    unrated_events = int(
        db.scalar(
            select(func.count(BillingUsageEvent.id))
            .outerjoin(
                BillingRatedItem,
                BillingRatedItem.usage_event_id == BillingUsageEvent.id,
            )
            .where(
                BillingUsageEvent.occurred_at >= period_start,
                BillingUsageEvent.occurred_at < period_end,
                (BillingRatedItem.id.is_(None) | (BillingRatedItem.state == "unrated")),
            )
        )
        or 0
    )
    unknown_reservations = int(
        db.scalar(
            select(func.count(BillingReservation.id)).where(
                BillingReservation.state == "unknown",
                BillingReservation.created_at < period_end,
            )
        )
        or 0
    )
    stale_held_reservations = int(
        db.scalar(
            select(func.count(BillingReservation.id)).where(
                BillingReservation.state == "held",
                BillingReservation.expires_at < datetime.now(UTC),
            )
        )
        or 0
    )
    reservation_overruns = int(
        db.scalar(
            select(func.count(BillingAuditEvent.id)).where(
                BillingAuditEvent.action == "reservation_overrun",
                BillingAuditEvent.created_at >= period_start,
                BillingAuditEvent.created_at < period_end,
            )
        )
        or 0
    )
    amount_rows = db.execute(
        select(
            BillingRatedItem.currency,
            func.coalesce(func.sum(BillingRatedItem.amount_minor_decimal), 0),
        )
        .join(
            BillingUsageEvent,
            BillingUsageEvent.id == BillingRatedItem.usage_event_id,
        )
        .where(
            BillingUsageEvent.occurred_at >= period_start,
            BillingUsageEvent.occurred_at < period_end,
            BillingRatedItem.state == "shadow",
        )
        .group_by(BillingRatedItem.currency)
    ).all()
    shadow_amounts = {row[0]: str(row[1]) for row in amount_rows}

    rated_rows = db.execute(
        select(BillingRatedItem.currency, BillingRatedItem.amount_minor_decimal)
        .join(
            BillingUsageEvent,
            BillingUsageEvent.id == BillingRatedItem.usage_event_id,
        )
        .where(
            BillingUsageEvent.occurred_at >= period_start,
            BillingUsageEvent.occurred_at < period_end,
            BillingRatedItem.state == "shadow",
        )
    ).all()
    rounded_rated_amounts: dict[str, int] = {}
    for currency, amount in rated_rows:
        rounded_rated_amounts[currency] = rounded_rated_amounts.get(currency, 0) + int(
            amount.to_integral_value(rounding=ROUND_HALF_UP)
        )

    ledger_rows = db.execute(
        select(BillingLedgerTransaction.currency, BillingLedgerPosting.amount_minor)
        .join(
            BillingLedgerPosting,
            BillingLedgerPosting.transaction_id == BillingLedgerTransaction.id,
        )
        .where(
            BillingLedgerTransaction.mode == "shadow",
            BillingLedgerTransaction.created_at >= period_start,
            BillingLedgerTransaction.created_at < period_end,
        )
    ).all()
    ledger_positive_amounts: dict[str, int] = {}
    for currency, amount in ledger_rows:
        if amount > 0:
            ledger_positive_amounts[currency] = (
                ledger_positive_amounts.get(currency, 0) + amount
            )
    currencies = set(rounded_rated_amounts) | set(ledger_positive_amounts)
    ledger_amount_mismatches = {
        currency: rounded_rated_amounts.get(currency, 0)
        - ledger_positive_amounts.get(currency, 0)
        for currency in currencies
        if rounded_rated_amounts.get(currency, 0)
        != ledger_positive_amounts.get(currency, 0)
    }

    report = {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "shadow_ledger_transactions": len(transaction_rows),
        "unbalanced_transactions": unbalanced,
        "unrated_events": unrated_events,
        "unknown_reservations": unknown_reservations,
        "stale_held_reservations": stale_held_reservations,
        "reservation_overruns": reservation_overruns,
        "shadow_amount_minor_by_currency": shadow_amounts,
        "rated_ledger_mismatch_minor_by_currency": ledger_amount_mismatches,
    }
    needs_review = bool(
        unbalanced
        or unrated_events
        or unknown_reservations
        or stale_held_reservations
        or ledger_amount_mismatches
        or reservation_overruns
    )
    run = BillingReconciliationRun(
        id=uuid4(),
        period_start=period_start,
        period_end=period_end,
        status="needs_review" if needs_review else "completed",
        report_json=report,
        created_by_user_id=actor_user_id,
        completed_at=datetime.now(UTC),
    )
    db.add(run)
    db.add(
        BillingAuditEvent(
            id=uuid4(),
            actor_user_id=actor_user_id,
            action="reconciliation_run",
            subject_type="billing_reconciliation_run",
            subject_id=str(run.id),
            reason="管理员执行影子计费对账",
            metadata_json={"status": run.status, "report": report},
        )
    )
    db.commit()
    db.refresh(run)
    return run


def mark_stale_reservations_unknown(
    db: Session,
    *,
    actor_user_id: UUID,
    now: datetime | None = None,
    batch_size: int = 500,
) -> int:
    current = now or datetime.now(UTC)
    rows = db.scalars(
        select(BillingReservation)
        .where(
            BillingReservation.state == "held",
            BillingReservation.expires_at <= current,
        )
        .order_by(BillingReservation.expires_at.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    ).all()
    for reservation in rows:
        reservation.state = "unknown"
        items = db.scalars(
            select(BillingReservationItem).where(
                BillingReservationItem.reservation_id == reservation.id,
                BillingReservationItem.state == "held",
            )
        ).all()
        for item in items:
            item.state = "unknown"
        db.add(
            BillingAuditEvent(
                id=uuid4(),
                tenant_id=reservation.tenant_id,
                actor_user_id=actor_user_id,
                action="stale_reservation_marked_unknown",
                subject_type="billing_reservation",
                subject_id=str(reservation.id),
                reason="预留超时且没有可信的最终结算结果，转入人工核对队列",
                metadata_json={
                    "request_key": reservation.event_key,
                    "unknown_resources": [item.resource_code for item in items],
                },
            )
        )
    db.commit()
    return len(rows)


def settle_unknown_reservation_with_usage(
    db: Session,
    *,
    reservation_id: UUID,
    actor_user_id: UUID,
    prompt_tokens: int,
    completion_tokens: int,
    reason: str,
) -> BillingReservation:
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise HTTPException(
            status_code=422,
            detail={"code": "reason_required", "message": "人工结算必须填写原因"},
        )
    reference = db.scalar(
        select(BillingReservation).where(BillingReservation.id == reservation_id)
    )
    if reference is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "reservation_not_found", "message": "预留记录不存在"},
        )
    db.scalar(
        select(Tenant).where(Tenant.id == reference.tenant_id).with_for_update()
    )
    reservation = db.scalar(
        select(BillingReservation)
        .where(BillingReservation.id == reservation_id)
        .with_for_update()
    )
    if reservation is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "reservation_not_found", "message": "预留记录不存在"},
        )
    if reservation.state != "unknown":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "reservation_not_unknown",
                "message": "只有 usage 未知的预留记录可以人工补录结算",
            },
        )
    if reservation.model_source != "platform":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "manual_token_settlement_not_applicable",
                "message": "只有平台模型的未知 Token 用量需要人工补录结算",
            },
        )
    if reservation.usage_record_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "usage_record_missing",
                "message": "该预留没有关联用量记录，无法安全补录 Token usage",
            },
        )
    usage_record = db.get(UsageRecord, reservation.usage_record_id)
    if usage_record is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "usage_record_missing",
                "message": "关联用量记录不存在，无法安全补录 Token usage",
            },
        )
    usage_record.prompt_tokens = prompt_tokens
    usage_record.completion_tokens = completion_tokens
    usage_record.total_tokens = prompt_tokens + completion_tokens
    db.add(
        BillingAuditEvent(
            id=uuid4(),
            tenant_id=reservation.tenant_id,
            actor_user_id=actor_user_id,
            action="manual_usage_settlement",
            subject_type="billing_reservation",
            subject_id=str(reservation.id),
            reason=normalized_reason[:500],
            metadata_json={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "request_key": reservation.event_key,
            },
        )
    )
    finalize_gateway_request(
        db,
        reservation_id=reservation.id,
        usage_record=usage_record,
        trusted_usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        usage_unknown=False,
        response_status=reservation.response_status,
        response_json=reservation.response_json,
    )
    db.refresh(reservation)
    return reservation


def release_unknown_reservation(
    db: Session,
    *,
    reservation_id: UUID,
    actor_user_id: UUID,
    reason: str,
) -> BillingReservation:
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise HTTPException(
            status_code=422,
            detail={"code": "reason_required", "message": "人工释放未知预留必须填写原因"},
        )
    reservation = db.scalar(
        select(BillingReservation)
        .where(BillingReservation.id == reservation_id)
        .with_for_update()
    )
    if reservation is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "reservation_not_found", "message": "预留记录不存在"},
        )
    if reservation.state != "unknown":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "reservation_not_unknown",
                "message": "只有 usage 未知的预留记录可以人工释放",
            },
        )
    items = db.scalars(
        select(BillingReservationItem).where(
            BillingReservationItem.reservation_id == reservation.id,
            BillingReservationItem.state == "unknown",
        )
    ).all()
    for item in items:
        item.state = "released"
        item.consumed_units = 0
    reservation.state = "released"
    reservation.settled_at = datetime.now(UTC)
    db.add(
        BillingAuditEvent(
            id=uuid4(),
            tenant_id=reservation.tenant_id,
            actor_user_id=actor_user_id,
            action="unknown_reservation_released",
            subject_type="billing_reservation",
            subject_id=str(reservation.id),
            reason=normalized_reason[:500],
            metadata_json={
                "released_resources": [item.resource_code for item in items],
                "request_key": reservation.event_key,
            },
        )
    )
    db.commit()
    db.refresh(reservation)
    return reservation
