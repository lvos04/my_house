"""Small shared DB helpers: settings (plain and encrypted), audit, alerts, role lookups, readings."""

from __future__ import annotations

import base64
import logging
import math
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from . import events
from .models import Alert, AuditLog, Device, DeviceBinding, LogicalRole, SensorReading, Setting

log = logging.getLogger(__name__)

# Cumulative counters: a negative value is always a read error and is rejected at ingest.
COUNTER_METRICS = frozenset(
    {
        "energy_import_kwh",
        "energy_export_kwh",
        "energy_import_t1_kwh",
        "energy_import_t2_kwh",
        "energy_export_t1_kwh",
        "energy_export_t2_kwh",
        "gas_m3",
        "water_m3",
    }
)


async def get_setting(db: AsyncSession, key: str, default: Any = None) -> Any:
    row = await db.get(Setting, key)
    return row.value.get("v", default) if row else default


async def set_setting(db: AsyncSession, key: str, value: Any) -> None:
    stmt = insert(Setting).values(key=key, value={"v": value})
    await db.execute(stmt.on_conflict_do_update(index_elements=[Setting.key], set_={"value": {"v": value}}))


def _box():
    from .config import get_settings
    from .security import SecretBox

    return SecretBox(get_settings().resolved_secret_key())


class SecretSettingError(ValueError):
    """An encrypted setting cannot be read with the current master key (or is damaged)."""


async def get_secret_setting(db: AsyncSession, key: str, default: Any = None) -> Any:
    """Read a setting stored encrypted with the master key (SecretBox). Raises ValueError
    (``SecretSettingError``) when the stored value cannot be decrypted with the current key, e.g. after
    a restore on a new NAS or a key change without ``shc-api rekey``, or when it is damaged.

    AES-GCM reports a wrong key as ``cryptography.exceptions.InvalidTag``, which is no ValueError;
    every caller handles ValueError only, so all decrypt failures are turned into one here."""
    raw = await get_setting(db, key)
    if not raw:
        return default
    box = _box()  # a missing master key stays a configuration error (RuntimeError)
    try:
        data = box.decrypt(base64.b64decode(raw, validate=True))
    except Exception as exc:  # InvalidTag (other key), binascii/JSON errors, unknown format
        raise SecretSettingError(f"Instelling {key} is niet te ontsleutelen met de huidige hoofdsleutel") from exc
    if not isinstance(data, dict):
        raise SecretSettingError(f"Instelling {key} heeft een onbekend formaat")
    return data.get("v", default)


async def set_secret_setting(db: AsyncSession, key: str, value: Any) -> None:
    """Store a setting encrypted at rest (None removes it)."""
    if value is None:
        await db.execute(Setting.__table__.delete().where(Setting.key == key))
        return
    await set_setting(db, key, base64.b64encode(_box().encrypt({"v": value})).decode())


# Settings whose value is SecretBox-encrypted (re-encrypted by `shc-api rekey`).
SECRET_SETTINGS = ("backup_passphrase_enc", "notify_url_enc")


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


async def resolve_alert(db: AsyncSession, key: str) -> list[Alert]:
    """Resolve the open alert(s) with this key and publish ``alert.resolved`` {id} for each."""
    resolved = []
    for a in (await db.execute(select(Alert).where(Alert.key == key, Alert.resolved_at.is_(None)))).scalars():
        a.resolved_at = datetime.now(UTC)
        resolved.append(a)
        await events.publish(db, "alert.resolved", a.home_id, {"id": a.id})
    return resolved


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
    """Idempotent insert; returns False for duplicates and for rejected values.

    Rejected: non-finite numbers, and negative values of cumulative counters (a counter can never be
    below zero; such a reading would otherwise show up as a reset or as negative consumption)."""
    if value is not None and not math.isfinite(value):
        log.warning("Meting %s geweigerd: geen geldig getal (%r)", metric, value)
        return False
    if value is not None and value < 0 and metric in COUNTER_METRICS:
        log.warning("Negatieve tellerstand geweigerd: %s = %s (apparaat %s)", metric, value, device_id)
        return False
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
            select(func.count()).select_from(Alert).where(and_(Alert.home_id == home_id, Alert.resolved_at.is_(None)))
        )
    ).scalar_one()
