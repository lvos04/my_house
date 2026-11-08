"""Shared view helpers: home shape, unit capabilities/state, schedule and outdoor-unit lookups."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import climate as cl
from ..models import ClimateZone, CommandRecord, Device, Home, ScheduleBlock, SensorReading

ENERGY_ROLES = [
    ("grid", "Huis totaal (P1)"),
    ("airco", "Airco (beide units)"),
    ("boiler", "Boiler"),
]

# Climate-related logical roles a device can be bound to (POST /devices/{id}/bind).
CLIMATE_ROLES = [
    ("outdoor_temp", "Buitentemperatuur"),
]

# Command statuses that may still change a unit: queued, being sent, or sent but not yet confirmed.
PENDING_STATUSES = ("queued", "sending", "sent")
# A pending command older than this no longer counts for the effective mode (it expired or failed).
PENDING_WINDOW = timedelta(minutes=5)
# An offline unit keeps its last known mode this long for outdoor-unit conflict checks.
OFFLINE_HOLD = timedelta(minutes=30)


def home_view(h: Home) -> dict:
    return {
        "id": h.id,
        "name": h.name,
        "timezone": h.timezone,
        "currency": h.currency,
        "active": h.active_to is None,
    }


def caps_of(unit: Device | None) -> cl.Capabilities:
    """Unit capabilities from ``Device.state["caps"]`` (written by the adapter); defaults only when absent."""
    c = (unit.state or {}).get("caps", {}) if unit else {}
    if not isinstance(c, dict):
        c = {}
    listed = set(unit.capabilities or []) if unit else set()
    modes = tuple(c.get("modes") or cl.ALL_MODES)
    if "off" not in modes:
        modes = ("off", *modes)
    fan = c.get("fan")
    swing = c.get("swing")
    return cl.Capabilities(
        modes=modes,
        min_c=float(c.get("min_c", 16)),
        max_c=float(c.get("max_c", 30)),
        step_c=float(c.get("step_c") or 1.0),
        fan=bool(fan) if fan is not None else ("fan" in listed if listed else None),
        swing=bool(swing) if swing is not None else ("swing" in listed if listed else None),
    )


def unit_state(unit: Device | None) -> cl.UnitState:
    if unit is None:
        return cl.UnitState(False, None, None)
    s = unit.state or {}
    mode = s.get("mode")
    if s.get("power") is False:
        mode = "off"
    return cl.UnitState(bool(unit.online), mode, s.get("target_c"), unit.last_seen)


def observed_baseline(unit: Device) -> dict:
    """The unit's current readback as a baseline dict (mode, target, fan)."""
    us = unit_state(unit)
    out = cl.observed_state(us)
    fan = (unit.state or {}).get("fan")
    if fan is not None:
        out["fan"] = fan
    return out


async def schedule_entries(db: AsyncSession, zone_id: uuid.UUID) -> list[cl.ScheduleEntry]:
    rows = (await db.execute(select(ScheduleBlock).where(ScheduleBlock.zone_id == zone_id))).scalars()
    return [cl.ScheduleEntry(tuple(b.days), b.start, b.target_c, b.mode, b.label) for b in rows]


async def pending_command(
    db: AsyncSession,
    device_id: uuid.UUID,
    now: datetime | None = None,
    exclude_id: uuid.UUID | None = None,
    with_mode: bool = True,
    created_before: datetime | None = None,
) -> CommandRecord | None:
    """Newest set_hvac command for a unit that may still change it (queued/sending/sent, not expired).

    ``created_before`` limits it to commands queued earlier: the executor re-checks a command
    against what was accepted before it; later ones get their own re-check when it is their turn.
    """
    now = now or datetime.now(UTC)
    q = (
        select(CommandRecord)
        .where(
            CommandRecord.device_id == device_id,
            CommandRecord.action == "set_hvac",
            CommandRecord.status.in_(PENDING_STATUSES),
            or_(CommandRecord.created_at >= now - PENDING_WINDOW, CommandRecord.updated_at >= now - PENDING_WINDOW),
        )
        .order_by(CommandRecord.created_at.desc())
    )
    if exclude_id is not None:
        q = q.where(CommandRecord.id != exclude_id)
    if created_before is not None:
        q = q.where(CommandRecord.created_at < created_before)
    if with_mode:
        q = q.where(CommandRecord.params.has_key("mode"))
    return (await db.execute(q.limit(1))).scalar_one_or_none()


async def effective_mode(
    db: AsyncSession,
    unit: Device | None,
    now: datetime | None = None,
    exclude_id: uuid.UUID | None = None,
    created_before: datetime | None = None,
) -> str | None:
    """The mode a unit runs in or is about to run in.

    Newest pending command with a mode, else the last readback. An offline unit keeps its
    last known mode for ``OFFLINE_HOLD``; after that it is unknown (None).
    """
    if unit is None:
        return None
    now = now or datetime.now(UTC)
    cmd = await pending_command(db, unit.id, now, exclude_id, created_before=created_before)
    if cmd is not None:
        return cmd.params.get("mode")
    st = unit_state(unit)
    if unit.online:
        return st.mode
    if unit.last_seen is not None and now - unit.last_seen < OFFLINE_HOLD:
        return st.mode
    return None


async def other_zone_modes(
    db: AsyncSession,
    zone: ClimateZone,
    now: datetime | None = None,
    exclude_id: uuid.UUID | None = None,
    created_before: datetime | None = None,
) -> list[str]:
    """Effective modes of the other units on the same outdoor unit (same home and outdoor group)."""
    if not zone.outdoor_group:
        return []
    rows = (
        await db.execute(
            select(Device)
            .join(ClimateZone, ClimateZone.unit_device_id == Device.id)
            .where(
                ClimateZone.home_id == zone.home_id,
                ClimateZone.outdoor_group == zone.outdoor_group,
                ClimateZone.id != zone.id,
            )
        )
    ).scalars()
    out = []
    for d in rows:
        if zone.unit_device_id is not None and d.id == zone.unit_device_id:
            continue
        m = await effective_mode(db, d, now, exclude_id, created_before)
        if m and m != "off":
            out.append(m)
    return out


def _measurement(r: SensorReading | None, unit: str, now: datetime, stale_after: float, quality: str | None = None):
    if r is None:
        return {"value": None, "unit": unit, "quality": "missing", "at": None, "stale": True}
    stale = (now - r.observed_at).total_seconds() > stale_after
    return {
        "value": r.value,
        "unit": unit,
        "quality": quality or r.quality,
        "at": r.observed_at,
        "stale": stale,
    }
