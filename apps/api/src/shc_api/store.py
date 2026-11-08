"""Small shared DB helpers: settings, audit, alerts, role lookups, latest readings."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import events
from .models import Alert, AuditLog, Device, DeviceBinding, LogicalRole, SensorReading, Setting


async def get_setting(db: AsyncSession, key: str, default: Any = None) -> Any:
    row = await db.get(Setting, key)
    return row.value.get("v", default) if row else default


async def set_setting(db: AsyncSession, key: str, value: Any) -> None:
    stmt = insert(Setting).values(key=key, value={"v": value})
    await db.execute(stmt.on_conflict_do_update(index_elements=[Setting.key], set_={"value": {"v": value}}))


async def audit(db: AsyncSession, actor: str, action: str, target: str | None = None, **detail: Any) -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, detail=detail))


async def raise_alert(
    db: AsyncSession, home_id: uuid.UUID | None, key: str, severity: str, title: str, detail: str, source: str
) -> Alert | None:
    """Open an alert once per key; returns the new alert or None if already open."""
    existing = (
        await db.execute(select(Alert).where(Alert.key == key, Alert.resolved_at.is_(None)))
    ).scalar_one_or_none()
    if existing:
        existing.detail = detail
        return None
    a = Alert(home_id=home_id, key=key, severity=severity, title=title, detail=detail, source=source)
    db.add(a)
    await db.flush()
    await events.publish(db, "alert.created", home_id, alert_view(a))
    return a


async def resolve_alert(db: AsyncSession, key: str) -> None:
    for a in (await db.execute(select(Alert).where(Alert.key == key, Alert.resolved_at.is_(None)))).scalars():
        a.resolved_at = datetime.now(UTC)


def alert_view(a: Alert) -> dict:
    return {
        "id": a.id,
        "severity": a.severity,
        "title": a.title,
        "detail": a.detail,
        "created_at": a.created_at,
        "acknowledged": a.acknowledged_at is not None,
        "source": a.source,
    }


async def roles_of(db: AsyncSession, device_id: uuid.UUID) -> tuple[frozenset[str], frozenset[str]]:
    """(role keys bound now, protected role keys among them)."""
    rows = (
        await db.execute(
            select(LogicalRole.key, LogicalRole.protected)
            .join(DeviceBinding, DeviceBinding.role_id == LogicalRole.id)
            .where(DeviceBinding.device_id == device_id, DeviceBinding.effective_to.is_(None))
        )
    ).all()
    return frozenset(r.key for r in rows), frozenset(r.key for r in rows if r.protected)


async def device_for_role(db: AsyncSession, home_id: uuid.UUID, key: str) -> Device | None:
    return (
        (
            await db.execute(
                select(Device)
                .join(DeviceBinding, DeviceBinding.device_id == Device.id)
                .join(LogicalRole, LogicalRole.id == DeviceBinding.role_id)
                .where(
                    LogicalRole.home_id == home_id,
                    LogicalRole.key == key,
                    DeviceBinding.effective_to.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )


async def devices_for_role_history(db: AsyncSession, home_id: uuid.UUID, key: str) -> list[DeviceBinding]:
    return list(
        (
            await db.execute(
                select(DeviceBinding)
                .join(LogicalRole, LogicalRole.id == DeviceBinding.role_id)
                .where(LogicalRole.home_id == home_id, LogicalRole.key == key)
                .order_by(DeviceBinding.effective_from)
            )
        ).scalars()
    )


async def latest_reading(db: AsyncSession, device_id: uuid.UUID | None, metric: str) -> SensorReading | None:
    if device_id is None:
        return None
    return (
        await db.execute(
            select(SensorReading)
            .where(SensorReading.device_id == device_id, SensorReading.metric == metric)
            .order_by(SensorReading.observed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def ingest_reading(
    db: AsyncSession,
    device_id: uuid.UUID,
    metric: str,
    value: float | None,
    unit: str,
    observed_at: datetime,
    quality: str = "measured",
    event_id: str | None = None,
    raw: dict | None = None,
) -> bool:
    """Idempotent insert; returns False for duplicates."""
    eid = event_id or f"{metric}@{observed_at.isoformat()}"
    stmt = (
        insert(SensorReading)
        .values(
            device_id=device_id,
            metric=metric,
            value=value,
            unit=unit,
            quality=quality,
            observed_at=observed_at,
            event_id=eid,
            raw=raw,
        )
        .on_conflict_do_nothing(constraint="uq_readings_event")
    )
    res = await db.execute(stmt)
    return (res.rowcount or 0) > 0


async def count_open_alerts(db: AsyncSession, home_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(Alert)
            .where(and_(Alert.home_id == home_id, Alert.resolved_at.is_(None)))
        )
    ).scalar_one()
