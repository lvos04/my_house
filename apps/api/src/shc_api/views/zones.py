"""Climate zone JSON shape (docs/api.md `Zone`)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from .. import climate as cl
from ..models import ClimateZone, Device, Home, Room
from ..store import latest_reading
from .common import _measurement, caps_of, other_zone_modes, schedule_entries, unit_state


async def zone_view(db: AsyncSession, zone: ClimateZone, home: Home, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    tz = ZoneInfo(home.timezone)
    pol = cl.policy_with_defaults(zone.policy)
    unit = await db.get(Device, zone.unit_device_id) if zone.unit_device_id else None
    sensor_dev = zone.sensor_device_id or zone.unit_device_id
    t = await latest_reading(db, sensor_dev, "temperature")
    h = await latest_reading(db, zone.sensor_device_id, "humidity")
    room = await db.get(Room, zone.room_id) if zone.room_id else None
    caps = caps_of(unit)
    us = unit_state(unit)
    others = await other_zone_modes(db, zone)
    sched = await schedule_entries(db, zone.id)
    nc = cl.next_change(sched, now, tz)
    valid, notice = cl.sensor_status(cl.SensorValue(t.value if t else None, t.observed_at if t else None), now, pol)
    if not us.online:
        notice = "Airco niet bereikbaar. Gebruik zo nodig de afstandsbediening."
    elif zone.control == "manual" and zone.override_source == "remote_control":
        notice = "Bediend met afstandsbediening; de automaat wacht."
    s = (unit.state or {}) if unit else {}
    return {
        "id": zone.id,
        "name": zone.name,
        "room": room.name if room else None,
        "unit_device_id": zone.unit_device_id,
        "sensor_device_id": zone.sensor_device_id,
        "online": us.online,
        "mode": us.mode or "off",
        "target_c": us.target_c,
        "fan": s.get("fan"),
        "temperature": _measurement(t, "°C", now, pol["stale_after_s"]),
        "humidity": _measurement(h, "%", now, pol["stale_after_s"]),
        "control": zone.control,
        "profile": zone.profile,
        "override": {"until": zone.override_until, "source": zone.override_source}
        if zone.control == "manual"
        else None,
        "capabilities": {
            "modes": list(caps.modes),
            "fan": "fan" in (unit.capabilities if unit else []),
            "swing": "swing" in (unit.capabilities if unit else []),
            "min_c": caps.min_c,
            "max_c": caps.max_c,
            "step_c": caps.step_c,
        },
        "blocked_modes": cl.blocked_modes(caps, others),
        "next_change": {"at": nc[0], "target_c": nc[1].target_c, "label": nc[1].label} if nc else None,
        "policy": {
            k: pol[k] for k in ("band_c", "min_interval_s", "max_step_c", "max_starts_per_hour", "stale_after_s")
        },
        "notice": notice if (notice and (not valid or not us.online or zone.control == "manual")) else None,
    }
