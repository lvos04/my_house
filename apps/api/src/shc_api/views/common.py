"""Shared view helpers: home shape, unit capabilities/state, schedule and outdoor-unit lookups."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import climate as cl
from ..models import ClimateZone, Device, Home, ScheduleBlock, SensorReading

ENERGY_ROLES = [
    ("grid", "Huis totaal (P1)"),
    ("airco", "Airco (beide units)"),
    ("boiler", "Boiler"),
]


def home_view(h: Home) -> dict:
    return {
        "id": h.id,
        "name": h.name,
        "timezone": h.timezone,
        "currency": h.currency,
        "active": h.active_to is None,
    }


def caps_of(unit: Device | None) -> cl.Capabilities:
    c = (unit.state or {}).get("caps", {}) if unit else {}
    return cl.Capabilities(
        modes=tuple(c.get("modes", ("off", "heat", "cool", "dry", "fan", "auto"))),
        min_c=float(c.get("min_c", 16)),
        max_c=float(c.get("max_c", 30)),
        step_c=float(c.get("step_c", 1.0)),
    )


def unit_state(unit: Device | None) -> cl.UnitState:
    if unit is None:
        return cl.UnitState(False, None, None)
    s = unit.state or {}
    mode = s.get("mode")
    if s.get("power") is False:
        mode = "off"
    return cl.UnitState(bool(unit.online), mode, s.get("target_c"), unit.last_seen)


async def schedule_entries(db: AsyncSession, zone_id: uuid.UUID) -> list[cl.ScheduleEntry]:
    rows = (await db.execute(select(ScheduleBlock).where(ScheduleBlock.zone_id == zone_id))).scalars()
    return [cl.ScheduleEntry(tuple(b.days), b.start, b.target_c, b.mode, b.label) for b in rows]


async def other_zone_modes(db: AsyncSession, zone: ClimateZone) -> list[str]:
    if not zone.outdoor_group:
        return []
    rows = (
        await db.execute(
            select(Device)
            .join(ClimateZone, ClimateZone.unit_device_id == Device.id)
            .where(ClimateZone.outdoor_group == zone.outdoor_group, ClimateZone.id != zone.id)
        )
    ).scalars()
    out = []
    for d in rows:
        st = unit_state(d)
        if st.online and st.mode and st.mode != "off":
            out.append(st.mode)
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
