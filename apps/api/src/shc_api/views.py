"""Builds the JSON shapes from docs/api.md."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import climate as cl
from . import energy as en
from .models import (
    Alert,
    ClimateZone,
    Device,
    DeviceBinding,
    Home,
    Integration,
    LogicalRole,
    Room,
    ScheduleBlock,
    SensorReading,
    Tariff,
)
from .store import alert_view, latest_reading

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


def _measurement(
    r: SensorReading | None, unit: str, now: datetime, stale_after: float, quality: str | None = None
):
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
    valid, notice = cl.sensor_status(
        cl.SensorValue(t.value if t else None, t.observed_at if t else None), now, pol
    )
    if not us.online:
        notice = "Airco niet bereikbaar. Gebruik zo nodig de afstandsbediening."
    elif zone.control == "manual" and zone.override_source == "remote_control":
        notice = "Bediend met afstandsbediening; de automaat wacht."
    s = (unit.state or {}) if unit else {}
    return {
        "id": zone.id,
        "name": zone.name,
        "room": room.name if room else None,
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
            k: pol[k]
            for k in ("band_c", "min_interval_s", "max_step_c", "max_starts_per_hour", "stale_after_s")
        },
        "notice": notice if (notice and (not valid or not us.online or zone.control == "manual")) else None,
    }


async def lights_view(db: AsyncSession, home_id: uuid.UUID) -> dict:
    devs = (
        await db.execute(
            select(Device).where(Device.home_id == home_id, Device.kind.in_(["light_group", "scene"]))
        )
    ).scalars()
    groups, scenes = [], []
    for d in devs:
        s = d.state or {}
        if d.kind == "light_group":
            groups.append(
                {
                    "id": d.id,
                    "name": d.name,
                    "on": bool(s.get("on")),
                    "brightness": s.get("brightness"),
                    "scene": s.get("scene"),
                    "reachable": d.online,
                    "lights_on": s.get("lights_on", 0),
                    "lights_total": s.get("lights_total", 0),
                }
            )
        else:
            scenes.append({"id": d.id, "name": d.name, "group_id": s.get("group_device_id")})
    groups.sort(key=lambda g: g["name"])
    return {"groups": groups, "scenes": scenes}


# --- energy -------------------------------------------------------------------


async def energy_series(
    db: AsyncSession, home_id: uuid.UUID, role: str, metric: str, buckets: list[en.Bucket]
) -> list[en.Bucket] | None:
    bindings = (
        (
            await db.execute(
                select(DeviceBinding)
                .join(LogicalRole, LogicalRole.id == DeviceBinding.role_id)
                .where(LogicalRole.home_id == home_id, LogicalRole.key == role)
            )
        )
        .scalars()
        .all()
    )
    if not bindings:
        return None
    out = [en.Bucket(b.start, b.end) for b in buckets]
    start, end = buckets[0].start, buckets[-1].end
    pad = timedelta(hours=2)
    for bnd in bindings:
        s = max(start - pad, bnd.effective_from)
        e = min(end + pad, bnd.effective_to) if bnd.effective_to else end + pad
        if e <= s:
            continue
        rows = (
            await db.execute(
                select(SensorReading.observed_at, SensorReading.value)
                .where(
                    SensorReading.device_id == bnd.device_id,
                    SensorReading.metric == metric,
                    SensorReading.observed_at >= s,
                    SensorReading.observed_at <= e,
                )
                .order_by(SensorReading.observed_at)
            )
        ).all()
        part = [en.Bucket(b.start, b.end) for b in buckets]
        en.aggregate([en.CounterPoint(r.observed_at, r.value) for r in rows if r.value is not None], part)
        for o, p in zip(out, part, strict=True):
            o.kwh += p.kwh
            o.covered_s += p.covered_s
            o.notes += [n for n in p.notes if n not in o.notes]
    for o in out:
        o.complete = (
            o.covered_s + 60 >= (o.end - o.start).total_seconds() and "ontbrekende metingen" not in o.notes
        )
    return out


async def tariff_at(db: AsyncSession, home_id: uuid.UUID, day: date) -> Tariff | None:
    return (
        await db.execute(
            select(Tariff)
            .where(Tariff.home_id == home_id, Tariff.valid_from <= day)
            .order_by(Tariff.valid_from.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def energy_summary(db: AsyncSession, home: Home, period: str, day: date) -> dict:
    tz = ZoneInfo(home.timezone)
    start, end, unit = en.period_bounds(period, day, tz)
    buckets = en.make_buckets(start, end, unit, tz)
    series: dict[str, list[en.Bucket]] = {}
    for role, _ in ENERGY_ROLES:
        s = await energy_series(db, home.id, role, "energy_import_kwh", buckets)
        if s is not None:
            series[role] = s
    exp = await energy_series(db, home.id, "grid", "energy_export_kwh", buckets)
    if exp is not None:
        series["grid_export"] = exp
    if "grid" in series:
        rest = []
        for i, b in enumerate(series["grid"]):
            v = b.kwh - sum(series[r][i].kwh for r in ("airco", "boiler") if r in series)
            rb = en.Bucket(b.start, b.end, max(0.0, v), b.covered_s, b.complete)
            rest.append(rb)
        series["rest"] = rest

    meters = []
    total_cost = 0.0
    have_cost = False
    labels = dict(ENERGY_ROLES) | {"grid_export": "Teruglevering", "rest": "Overig huis"}
    for role, bs in series.items():
        kwh = sum(b.kwh for b in bs)
        c = 0.0
        priced = True
        for b in bs:
            t = await tariff_at(db, home.id, b.start.date())
            if t is None:
                priced = False
                break
            c += b.kwh * float(t.export_per_kwh if role == "grid_export" else t.import_per_kwh)
        quality = (
            "derived"
            if role == "rest"
            else ("measured" if all(b.complete for b in bs if b.end <= datetime.now(tz)) else "estimated")
        )
        note = None
        if role == "rest":
            note = "P1 min airco en boiler; bestaat uit niet-gemeten apparaten."
        elif role == "airco":
            note = "Verbruik per binnenunit wordt niet gemeten (gedeelde compressor)."
        meters.append(
            {
                "role": role,
                "label": labels[role],
                "kwh": round(kwh, 3),
                "cost": round(c, 2) if priced else None,
                "quality": quality,
                "note": note,
            }
        )
        if role == "grid" and priced:
            total_cost += c
            have_cost = True
        if role == "grid_export" and priced:
            total_cost -= c
    total = sum(b.kwh for b in series.get("grid", []))
    now = datetime.now(tz)
    return {
        "period": period,
        "start": start,
        "end": end,
        "total_kwh": round(total, 3),
        "cost": round(total_cost, 2) if have_cost else None,
        "currency": home.currency,
        "meters": meters,
        "buckets": [
            {
                "start": buckets[i].start,
                "complete": all(series[r][i].complete for r in series) and buckets[i].end <= now,
                "values": {r: round(series[r][i].kwh, 4) for r in series},
            }
            for i in range(len(buckets))
        ],
    }


async def live_power(db: AsyncSession, home_id: uuid.UUID) -> dict | None:
    from .store import device_for_role

    now = datetime.now(UTC)
    parts = []
    total = None
    for role, label in ENERGY_ROLES:
        d = await device_for_role(db, home_id, role)
        r = await latest_reading(db, d.id if d else None, "power_w")
        if r is None or (now - r.observed_at) > timedelta(minutes=5):
            continue
        if role == "grid":
            total = r.value
        else:
            parts.append(
                {"role": role, "label": label, "watts": r.value, "quality": "measured", "at": r.observed_at}
            )
    if total is None and not parts:
        return None
    if total is not None:
        rest = max(0.0, total - sum(p["watts"] or 0 for p in parts))
        parts.append(
            {"role": "rest", "label": "Overig", "watts": round(rest, 1), "quality": "derived", "at": now}
        )
    return {"at": now, "total_w": total, "parts": parts}


async def status_checks(db: AsyncSession, home_id: uuid.UUID | None = None) -> list[dict]:
    q = select(Integration)
    if home_id:
        q = q.where(Integration.home_id == home_id)
    labels = {"hue": "Hue Bridge", "gree": "Gree airco", "homewizard": "HomeWizard", "zigbee": "Zigbee"}
    out = []
    for i in (await db.execute(q)).scalars():
        out.append(
            {
                "key": f"integration:{i.id}",
                "label": labels.get(i.provider, i.name),
                "state": i.state if i.state in ("ok", "warn", "error") else "unknown",
                "detail": i.detail or i.name,
            }
        )
    return out


async def open_alerts(db: AsyncSession, home_id: uuid.UUID, limit: int = 20) -> list[dict]:
    rows = (
        await db.execute(
            select(Alert)
            .where(Alert.home_id == home_id, Alert.resolved_at.is_(None))
            .order_by(Alert.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [alert_view(a) for a in rows]
