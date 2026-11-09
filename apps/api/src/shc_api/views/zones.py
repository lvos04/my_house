"""Climate zone JSON shape (docs/api.md `Zone`)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from .. import climate as cl
from ..models import ClimateZone, ComfortProfile, CommandRecord, Device, Home, Room
from ..store import latest_reading
from .common import _measurement, caps_of, effective_mode, other_zone_modes, schedule_entries, unit_state

POLICY_KEYS = (
    "band_c",
    "min_interval_s",
    "max_step_c",
    "max_starts_per_hour",
    "stale_after_s",
    "plausible_min_c",
    "plausible_max_c",
    "max_offset_c",
    "override_default_minutes",
    "energy_priority",
    "watchdog_minutes",
    "watchdog_min_progress_c",
)


async def zone_view(db: AsyncSession, zone: ClimateZone, home: Home, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    tz = ZoneInfo(home.timezone)
    pol = cl.policy_with_defaults(zone.policy)
    unit = await db.get(Device, zone.unit_device_id) if zone.unit_device_id else None
    linked = unit is not None
    sensor_dev = zone.sensor_device_id or zone.unit_device_id
    t = await latest_reading(db, sensor_dev, "temperature")
    h = await latest_reading(db, zone.sensor_device_id, "humidity")
    room = await db.get(Room, zone.room_id) if zone.room_id else None
    profile = await db.get(ComfortProfile, zone.profile_id) if zone.profile_id else None
    caps = caps_of(unit)
    us = unit_state(unit)
    others = await other_zone_modes(db, zone, now) if linked else []
    current = await effective_mode(db, unit, now) if linked else None
    sched = await schedule_entries(db, zone.id)
    nc = cl.next_change(sched, now, tz)
    valid, sensor_notice = cl.sensor_status(
        cl.SensorValue(t.value if t else None, t.observed_at if t else None), now, pol
    )
    pending = await db.get(CommandRecord, zone.pending_command_id) if zone.pending_command_id else None

    # One short, plain notice; "info" is neutral, "warn" needs attention.
    notice, level = None, None
    if not linked:
        notice = (
            "Nog geen airco gekoppeld; alleen de sensorwaarden worden getoond. Koppel een airco via Apparaten."
            if zone.sensor_device_id
            else "Nog geen airco gekoppeld. Koppel een airco via Apparaten."
        )
        level = "info"
    elif not us.online:
        notice, level = "Airco niet bereikbaar. Gebruik zo nodig de afstandsbediening.", "warn"
    elif zone.control == "manual" and zone.override_source == "remote_control":
        notice, level = "Bediend met afstandsbediening; de automaat wacht.", "info"
    elif zone.control == "off":
        by = " met de afstandsbediening" if zone.override_source == "remote_control" else ""
        notice, level = f"Uitgezet{by}; kies 'Terug naar schema' om het schema weer te volgen.", "info"
    elif not valid and (zone.sensor_device_id or zone.control == "manual"):
        notice, level = sensor_notice, "warn"
    elif zone.control in ("schedule", "oem_fallback") and not sched:
        notice, level = "Geen schema ingesteld; de airco houdt zijn eigen instelling.", "info"

    s = (unit.state or {}) if unit else {}
    unit_status = "not_linked" if not linked else ("online" if us.online else "offline")
    fan_cap = caps.fan if caps.fan is not None else ("fan" in (unit.capabilities if unit else []))
    swing_cap = caps.swing if caps.swing is not None else ("swing" in (unit.capabilities if unit else []))
    return {
        "id": zone.id,
        "name": zone.name,
        "room": room.name if room else None,
        "unit_device_id": zone.unit_device_id,
        "sensor_device_id": zone.sensor_device_id,
        "unit_linked": linked,
        "unit_status": unit_status,
        "online": us.online,
        "mode": us.mode or "off",
        "target_c": us.target_c,
        "fan": s.get("fan"),
        "temperature": _measurement(t, "°C", now, pol["stale_after_s"]),
        "humidity": _measurement(h, "%", now, pol["stale_after_s"]),
        "control": zone.control,
        "profile": profile.name if profile else zone.profile,
        "profile_id": zone.profile_id,
        "override": {"until": zone.override_until, "source": zone.override_source}
        if zone.control == "manual"
        else None,
        "can_resume": linked and zone.control in ("manual", "off"),
        "setpoint_adjustable": linked and us.online and current not in (None, "off"),
        "pending_command": {"id": pending.id, "status": pending.status, "since": zone.pending_since}
        if pending is not None
        else None,
        "capabilities": {
            "modes": list(caps.modes) if linked else [],
            "fan": bool(fan_cap) if linked else False,
            "swing": bool(swing_cap) if linked else False,
            "min_c": caps.min_c,
            "max_c": caps.max_c,
            "step_c": caps.step_c,
        },
        "blocked_modes": cl.blocked_modes(caps, others),
        "next_change": {"at": nc[0], "target_c": nc[1].target_c, "mode": nc[1].mode, "label": nc[1].label}
        if nc
        else None,
        "correction_c": round(zone.correction_c or 0.0, 2),
        "energy_priority": pol["energy_priority"],
        "last_decision": zone.last_decision,
        "watchdog_since": zone.watchdog_since,
        "policy": {k: pol[k] for k in POLICY_KEYS},
        "notice": notice,
        "notice_level": level,
    }
