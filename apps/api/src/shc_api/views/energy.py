"""Energy JSON shapes: per-role series, period summary with costs, live power."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import energy as en
from ..models import DeviceBinding, Home, LogicalRole, SensorReading, Tariff
from ..store import latest_reading
from .common import ENERGY_ROLES


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
        o.complete = o.covered_s + 60 >= (o.end - o.start).total_seconds() and "ontbrekende metingen" not in o.notes
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
    from ..store import device_for_role

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
            parts.append({"role": role, "label": label, "watts": r.value, "quality": "measured", "at": r.observed_at})
    if total is None and not parts:
        return None
    if total is not None:
        rest = max(0.0, total - sum(p["watts"] or 0 for p in parts))
        parts.append({"role": "rest", "label": "Overig", "watts": round(rest, 1), "quality": "derived", "at": now})
    return {"at": now, "total_w": total, "parts": parts}
