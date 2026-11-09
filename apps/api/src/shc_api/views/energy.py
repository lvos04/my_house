"""Energy JSON shapes: per-role series, period summary with costs, live power, export rows.

Summaries read the worker's hourly aggregates (``energy_hourly``) for everything before the
aggregation frontier of a meter, and compute only the remaining tail (normally the current hour)
from raw readings. Without aggregates (fresh install, backfill still running) the raw path is used
for the whole period, in one pass and in a thread when there is a lot of data.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import energy as en
from ..models import Device, DeviceBinding, EnergyHourly, Home, LogicalRole, SensorReading, Tariff
from ..store import device_for_role, get_setting, latest_reading
from .common import ENERGY_ROLES

LIVE_STALE = timedelta(minutes=5)
RAW_IN_THREAD = 2000  # points; above this the cleaning/distribution runs in a worker thread
REST_TOLERANCE_KWH = 0.01
REST_TOLERANCE_W = 50.0

LABELS = dict(ENERGY_ROLES) | {"grid_export": "Teruglevering", "rest": "Overig huis", "gas": "Gas"}
NOTES = {
    "rest": "P1 min airco en boiler; bestaat uit niet-gemeten apparaten.",
    "airco": "Verbruik per binnenunit wordt niet gemeten (gedeelde compressor).",
}

_AGG_GROUPED = text(
    """
    SELECT date_trunc(:unit, hour_start, :tz) AS b,
           sum(kwh * frac) AS kwh,
           sum(frac) AS hours,
           bool_or(flags @> '["incomplete"]'::jsonb) AS incomplete,
           bool_or(flags @> '["gap"]'::jsonb) AS gap,
           bool_or(flags @> '["suspect"]'::jsonb) AS suspect,
           bool_or(flags @> '["reset"]'::jsonb) AS reset
    FROM (
        SELECT hour_start, kwh, flags,
               (GREATEST(0, EXTRACT(EPOCH FROM (LEAST(hour_start + interval '1 hour', :hi)
                                               - GREATEST(hour_start, :lo)))) / 3600.0)::float8 AS frac
        FROM energy_hourly
        WHERE device_id = :d AND metric = :m AND hour_start > :lo_minus AND hour_start < :hi
    ) x
    WHERE frac > 0
    GROUP BY 1
    """
)


# --- context ------------------------------------------------------------------------------


@dataclass(slots=True)
class EnergyContext:
    """Per-request caches: rate overrides, devices and aggregation frontiers."""

    overrides: dict = field(default_factory=dict)
    devices: dict = field(default_factory=dict)
    frontiers: dict = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    async def load(cls, db: AsyncSession) -> EnergyContext:
        return cls(overrides=await rate_overrides(db))

    async def device(self, db: AsyncSession, device_id: uuid.UUID) -> Device | None:
        if device_id not in self.devices:
            self.devices[device_id] = await db.get(Device, device_id)
        return self.devices[device_id]

    async def frontier(self, db: AsyncSession, device_id: uuid.UUID, metric: str) -> datetime | None:
        k = (device_id, metric)
        if k not in self.frontiers:
            self.frontiers[k] = await aggregation_frontier(db, device_id, metric)
        return self.frontiers[k]


async def rate_overrides(db: AsyncSession) -> dict:
    """Admin-configurable maximum power per role, e.g. {"airco": 3.7} (Setting "energy_max_kw")."""
    v = await get_setting(db, "energy_max_kw", {}) or {}
    return {k: float(x) for k, x in v.items() if isinstance(x, int | float) and x > 0}


def device_rate(metric: str, role: str | None, device: Device | None, overrides: dict) -> float:
    cal = (device.calibration or {}) if device is not None else {}
    if metric != en.GAS_METRIC and isinstance(cal.get("max_kw"), int | float) and cal["max_kw"] > 0:
        return float(cal["max_kw"])
    return en.max_rate_for(metric, role, device.kind if device else None, overrides)


async def aggregation_frontier(db: AsyncSession, device_id: uuid.UUID, metric: str) -> datetime | None:
    """End of the aggregated range of one meter series: hours before this are in energy_hourly."""
    last = (
        await db.execute(
            select(func.max(EnergyHourly.hour_start)).where(
                EnergyHourly.device_id == device_id, EnergyHourly.metric == metric
            )
        )
    ).scalar_one_or_none()
    return last + timedelta(hours=1) if last else None


async def bindings_for(db: AsyncSession, home_id: uuid.UUID, role: str) -> list[DeviceBinding]:
    return list(
        (
            await db.execute(
                select(DeviceBinding)
                .join(LogicalRole, LogicalRole.id == DeviceBinding.role_id)
                .where(LogicalRole.home_id == home_id, LogicalRole.key == role)
                .order_by(DeviceBinding.effective_from)
            )
        ).scalars()
    )


# --- raw readings -------------------------------------------------------------------------


async def raw_points(
    db: AsyncSession, device_id: uuid.UUID, metric: str, lo: datetime, hi: datetime
) -> list[en.CounterPoint]:
    """Readings in [lo, hi) plus the last one before and the first few after, so usage across the
    edges of the period (also after a long outage) is not lost (verbeterlijst: rand van een dag)."""
    base = select(SensorReading.observed_at, SensorReading.value).where(
        SensorReading.device_id == device_id, SensorReading.metric == metric, SensorReading.value.is_not(None)
    )
    before = (
        await db.execute(base.where(SensorReading.observed_at < lo).order_by(SensorReading.observed_at.desc()).limit(1))
    ).all()
    inside = (
        await db.execute(
            base.where(SensorReading.observed_at >= lo, SensorReading.observed_at < hi).order_by(
                SensorReading.observed_at
            )
        )
    ).all()
    after = (
        await db.execute(
            base.where(SensorReading.observed_at >= hi)
            .order_by(SensorReading.observed_at)
            .limit(en.CONFIRM_READINGS + 1)
        )
    ).all()
    return [en.CounterPoint(r.observed_at, r.value) for r in (*before, *inside, *after)]


def _clean_and_distribute(points, rate, gap, buckets, lo_ts, hi_ts) -> None:
    en.distribute(en.clean_intervals(points, rate, gap), buckets, lo_ts, hi_ts)


async def _raw_into(
    db: AsyncSession,
    device_id: uuid.UUID,
    metric: str,
    lo: datetime,
    hi: datetime,
    rate: float,
    buckets: list[en.Bucket],
) -> None:
    points = await raw_points(db, device_id, metric, lo, hi)
    if len(points) < 2:
        return
    args = (points, rate, en.max_gap_for(metric), buckets, lo.timestamp(), hi.timestamp())
    if len(points) > RAW_IN_THREAD:
        await asyncio.to_thread(_clean_and_distribute, *args)
    else:
        _clean_and_distribute(*args)


async def _agg_into(
    db: AsyncSession,
    device_id: uuid.UUID,
    metric: str,
    lo: datetime,
    hi: datetime,
    buckets: list[en.Bucket],
    unit: str | None,
    tz: str,
) -> None:
    """Add aggregated hours in [lo, hi) to buckets. `unit` hour/day groups in SQL by local
    hour/day; None sums per bucket (arbitrary bucket bounds)."""
    if hi <= lo:
        return
    if unit is None:
        for b in buckets:
            s, e = max(lo, b.start), min(hi, b.end)
            if e > s:
                await _agg_rows_into(db, device_id, metric, s, e, [b], "hour", tz, single=True)
        return
    await _agg_rows_into(db, device_id, metric, lo, hi, buckets, unit, tz)


async def _agg_rows_into(db, device_id, metric, lo, hi, buckets, unit, tz, single: bool = False) -> None:
    rows = (
        await db.execute(
            _AGG_GROUPED,
            {
                "unit": unit,
                "tz": tz,
                "d": device_id,
                "m": metric,
                "lo": lo,
                "hi": hi,
                "lo_minus": lo - timedelta(hours=1),
            },
        )
    ).all()
    index = {round(b.start_ts): b for b in buckets}
    for r in rows:
        b = buckets[0] if single else index.get(round(r.b.timestamp()))
        if b is None:  # e.g. half-hour time zones: fall back to the bucket containing it
            ts = r.b.timestamp()
            b = next((x for x in buckets if x.start_ts <= ts < x.end_ts), None)
            if b is None:
                continue
        b.kwh += float(r.kwh or 0.0)
        b.covered_s += float(r.hours or 0.0) * 3600.0
        for flag, on in (
            (en.F_INCOMPLETE, r.incomplete),
            (en.F_GAP, r.gap),
            (en.F_SUSPECT, r.suspect),
            (en.F_RESET, r.reset),
        ):
            if on:
                b.add_flag(flag)


async def binding_into(
    db: AsyncSession,
    ctx: EnergyContext,
    device_id: uuid.UUID,
    role: str,
    metric: str,
    lo: datetime,
    hi: datetime,
    buckets: list[en.Bucket],
    unit: str | None,
    tz: str,
) -> None:
    """One meter (device) for the part of [lo, hi) where it is bound: aggregates, then raw tail."""
    if hi <= lo:
        return
    device = await ctx.device(db, device_id)
    f = await ctx.frontier(db, device_id, metric)
    current_hour = en.floor_hour(ctx.now)
    cut = min(f, current_hour) if f else lo
    cut = max(lo, min(cut, hi))
    await _agg_into(db, device_id, metric, lo, cut, buckets, unit, tz)
    if cut < hi:
        rate = device_rate(metric, role, device, ctx.overrides)
        await _raw_into(db, device_id, metric, cut, hi, rate, buckets)


async def role_series(
    db: AsyncSession,
    home_id: uuid.UUID,
    role: str,
    metric: str,
    buckets: list[en.Bucket],
    unit: str | None = None,
    tz: str = "UTC",
    ctx: EnergyContext | None = None,
) -> list[en.Bucket] | None:
    """Per-bucket totals of a role, over all devices that were bound to it (each only within its
    binding period, so a replaced meter is never counted twice). None when the role was never bound."""
    bindings = await bindings_for(db, home_id, role)
    if not bindings or not buckets:
        return None
    ctx = ctx or await EnergyContext.load(db)
    out = [en.Bucket(b.start, b.end) for b in buckets]
    start, end = buckets[0].start, buckets[-1].end
    for bnd in bindings:
        lo = max(start, bnd.effective_from)
        hi = min(end, bnd.effective_to) if bnd.effective_to else end
        await binding_into(db, ctx, bnd.device_id, role, metric, lo, hi, out, unit, tz)
    for b in out:
        b.finalize()
    return out


async def energy_series(
    db: AsyncSession, home_id: uuid.UUID, role: str, metric: str, buckets: list[en.Bucket]
) -> list[en.Bucket] | None:
    """Arbitrary buckets (e.g. experiment periods); aggregates summed per bucket."""
    return await role_series(db, home_id, role, metric, buckets, None)


# --- tariffs -------------------------------------------------------------------------------


async def tariff_extras(db: AsyncSession) -> dict:
    """Optional per-tariff extras without a schema change (Setting "tariff_extras"): {tariff_id: {gas_per_m3}}."""
    return await get_setting(db, "tariff_extras", {}) or {}


async def load_tariffs(db: AsyncSession, home_id: uuid.UUID) -> en.TariffTable:
    extras = await tariff_extras(db)
    rows = (await db.execute(select(Tariff).where(Tariff.home_id == home_id))).scalars()

    def f(v):
        return None if v is None else float(v)

    return en.TariffTable(
        en.TariffPeriod(
            valid_from=t.valid_from,
            import_per_kwh=float(t.import_per_kwh),
            export_per_kwh=float(t.export_per_kwh or 0),
            fixed_per_day=float(t.fixed_per_day or 0),
            import_t1_per_kwh=f(t.import_t1_per_kwh),
            import_t2_per_kwh=f(t.import_t2_per_kwh),
            gas_per_m3=f((extras.get(str(t.id)) or {}).get("gas_per_m3")),
            id=t.id,
        )
        for t in rows
    )


async def tariff_at(db: AsyncSession, home_id: uuid.UUID, day: date) -> Tariff | None:
    return (
        await db.execute(
            select(Tariff)
            .where(Tariff.home_id == home_id, Tariff.valid_from <= day)
            .order_by(Tariff.valid_from.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


# --- summary -------------------------------------------------------------------------------


def _has_data(bs: list[en.Bucket] | None) -> bool:
    return bool(bs) and any(b.covered_s > 0 or b.kwh for b in bs)


def _rest(series: dict[str, list[en.Bucket]]) -> tuple[list[en.Bucket], list[str]]:
    """Grid minus the sub-meters, per bucket. A missing or incomplete sub-meter makes rest
    'estimated' instead of silently dropping it."""
    grid = series["grid"]
    subs = [r for r in ("airco", "boiler") if r in series]
    out: list[en.Bucket] = []
    issues: list[str] = []
    for i, g in enumerate(grid):
        v = g.kwh - sum(series[r][i].kwh for r in subs)
        rb = en.Bucket(g.start, g.end, max(0.0, v), g.covered_s)
        rb.flags = set(g.flags)
        rb.notes = list(g.notes)
        for r in subs:
            sb = series[r][i]
            rb.flags |= sb.flags
            if not sb.complete and g.complete:
                rb.add_flag(en.F_ESTIMATED)
                msg = f"{LABELS[r]} mist metingen; overig is geschat."
                if msg not in rb.notes:
                    rb.notes.append(msg)
                if LABELS[r] not in issues:
                    issues.append(LABELS[r])
        if v < -REST_TOLERANCE_KWH:
            rb.add_flag(en.F_ESTIMATED)
            msg = "Tussenmeters samen hoger dan P1; overig op 0 gezet."
            if msg not in rb.notes:
                rb.notes.append(msg)
        rb.complete = g.complete and all(series[r][i].complete for r in subs)
        out.append(rb)
    return out, issues


def _local_day(b: en.Bucket, tz: ZoneInfo) -> date:
    return b.start.astimezone(tz).date()


async def energy_summary(db: AsyncSession, home: Home, period: str, day: date) -> dict:
    tz = ZoneInfo(home.timezone)
    start, end, unit = en.period_bounds(period, day, tz)
    buckets = en.make_buckets(start, end, unit, tz)
    fine_unit = "hour" if unit == "hour" else "day"
    fine = [en.Bucket(b.start, b.end) for b in buckets] if unit != "month" else en.make_buckets(start, end, "day", tz)
    ctx = await EnergyContext.load(db)
    now = ctx.now
    tariffs = await load_tariffs(db, home.id)

    async def series_of(role: str, metric: str) -> list[en.Bucket] | None:
        return await role_series(db, home.id, role, metric, fine, fine_unit, home.timezone, ctx)

    series: dict[str, list[en.Bucket]] = {}
    for role, _ in ENERGY_ROLES:
        s = await series_of(role, "energy_import_kwh")
        if s is not None:
            series[role] = s
    exp = await series_of("grid", "energy_export_kwh")
    if exp is not None:
        series["grid_export"] = exp
    rest_issues: list[str] = []
    if "grid" in series:
        series["rest"], rest_issues = _rest(series)
    t1 = t2 = None
    if "grid" in series and any(t.import_t1_per_kwh is not None for t in tariffs.periods):
        t1 = await series_of("grid", "energy_import_t1_kwh")
        t2 = await series_of("grid", "energy_import_t2_kwh")
    gas = await series_of("gas", en.GAS_METRIC)
    if not _has_data(gas):
        gas = await series_of("grid", en.GAS_METRIC)
    if not _has_data(gas):
        gas = None

    # --- prices per fine bucket (hour or day): tariffs split at their start date ---
    prices: list[en.TariffPeriod | None] = [tariffs.at(_local_day(b, tz)) for b in fine]
    import_price: list[float | None] = []
    for i, t in enumerate(prices):
        if t is None:
            import_price.append(None)
        else:
            a = t1[i].kwh if t1 else 0.0
            b = t2[i].kwh if t2 else 0.0
            import_price.append(en.import_price(t, a, b))

    def role_cost(role: str, bs: list[en.Bucket]) -> tuple[float | None, float, list[float | None]]:
        total = 0.0
        priced_kwh = 0.0
        per: list[float | None] = []
        any_price = False
        for i, b in enumerate(bs):
            t = prices[i]
            if t is None:
                per.append(None)
                continue
            p = t.export_per_kwh if role == "grid_export" else import_price[i]
            c = b.kwh * (p or 0.0)
            per.append(c)
            total += c
            priced_kwh += b.kwh
            any_price = True
        kwh = sum(b.kwh for b in bs)
        if kwh > 0:
            coverage = priced_kwh / kwh
        else:
            coverage = sum(1 for p in prices if p is not None) / len(prices) if prices else 0.0
        return (total if any_price else None), coverage, per

    meters = []
    costs_per_role: dict[str, list[float | None]] = {}
    grid_cost = export_rev = None
    grid_cov = 1.0
    for role, bs in series.items():
        kwh = sum(b.kwh for b in bs)
        c, cov, per = role_cost(role, bs)
        costs_per_role[role] = per
        finished = [b for b in bs if b.end <= now]
        suspect = any(en.F_SUSPECT in b.flags for b in bs)
        if role == "rest":
            quality = "estimated" if (rest_issues or any(en.F_ESTIMATED in b.flags for b in bs)) else "derived"
        else:
            quality = "measured" if all(b.complete for b in finished) and not suspect else "estimated"
        notes: list[str] = []
        for b in bs:
            notes += [n for n in b.notes if n not in notes]
        note = NOTES.get(role)
        if role == "rest" and rest_issues:
            note = f"{NOTES['rest']} Onvolledig: {', '.join(rest_issues)} mist metingen, dus overig is geschat."
        meters.append(
            {
                "role": role,
                "label": LABELS[role],
                "kwh": round(kwh, 3),
                "unit": "kWh",
                "cost": round(c, 2) if c is not None else None,
                "cost_coverage": round(cov, 3) if c is not None else 0.0,
                "quality": quality,
                "note": note,
                "notes": notes,
            }
        )
        if role == "grid":
            grid_cost, grid_cov = c, cov
        if role == "grid_export":
            export_rev = c

    # --- fixed costs: per day of the period that has started and has a tariff ---
    fixed = 0.0
    fixed_days = 0
    d = start.date()
    while d < end.date():
        if datetime.combine(d, time(0), tz) <= now:
            t = tariffs.at(d)
            if t is not None:
                fixed += t.fixed_per_day
                fixed_days += 1
        d += timedelta(days=1)

    gas_out = None
    gas_cost = None
    if gas is not None:
        gm3 = sum(b.kwh for b in gas)
        gc = 0.0
        g_priced = 0.0
        any_gp = False
        for i, b in enumerate(gas):
            t = prices[i]
            if t is not None and t.gas_per_m3 is not None:
                gc += b.kwh * t.gas_per_m3
                g_priced += b.kwh
                any_gp = True
        gas_cost = gc if any_gp else None
        finished = [b for b in gas if b.end <= now]
        gas_out = {
            "m3": round(gm3, 3),
            "unit": "m³",
            "cost": round(gc, 2) if any_gp else None,
            "cost_coverage": round(g_priced / gm3, 3) if any_gp and gm3 > 0 else (1.0 if any_gp else 0.0),
            "quality": "measured" if all(b.complete for b in finished) else "estimated",
            "note": None if any_gp else "Geen gasprijs ingesteld bij het tarief.",
        }

    total_cost = None
    if grid_cost is not None:
        total_cost = grid_cost - (export_rev or 0.0) + fixed + (gas_cost or 0.0)
    cost_note = None
    if grid_cost is not None and grid_cov < 0.999:
        cost_note = f"Kosten gedeeltelijk: {grid_cov:.0%} van het verbruik valt onder een tarief."
    elif grid_cost is None and "grid" in series and tariffs:
        cost_note = "Geen tarief geldig in deze periode."

    # --- roll fine buckets up into the output buckets (year: days -> months) ---
    if unit == "month":
        idx = []
        j = 0
        for fb in fine:
            while j + 1 < len(buckets) and buckets[j + 1].start_ts <= fb.start_ts:
                j += 1
            idx.append(j)
    else:
        idx = list(range(len(fine)))

    out_buckets = []
    for bi, b in enumerate(buckets):
        members = [i for i, x in enumerate(idx) if x == bi] if unit == "month" else [bi]
        values = {}
        complete = b.end <= now
        notes: list[str] = []
        for role, bs in series.items():
            values[role] = round(sum(bs[i].kwh for i in members), 4)
            complete = complete and all(bs[i].complete for i in members)
            for i in members:
                notes += [n for n in bs[i].notes if n not in notes]
        cost_parts = [costs_per_role.get("grid", [None] * len(fine))[i] for i in members]
        exp_parts = [costs_per_role.get("grid_export", [None] * len(fine))[i] for i in members]
        bc = None
        if any(c is not None for c in cost_parts):
            bc = round(sum(c or 0.0 for c in cost_parts) - sum(c or 0.0 for c in exp_parts), 2)
        item = {
            "start": b.start,
            "end": b.end,
            "complete": complete,
            "values": values,
            "cost": bc,
            "notes": notes,
        }
        if gas is not None:
            item["gas_m3"] = round(sum(gas[i].kwh for i in members), 4)
        out_buckets.append(item)

    total = sum(b.kwh for b in series.get("grid", []))
    return {
        "period": period,
        "start": start,
        "end": end,
        "timezone": home.timezone,
        "total_kwh": round(total, 3),
        "cost": round(total_cost, 2) if total_cost is not None else None,
        "cost_coverage": round(grid_cov, 3) if grid_cost is not None else 0.0,
        "fixed_cost": round(fixed, 2) if grid_cost is not None else None,
        "fixed_days": fixed_days,
        "cost_note": cost_note,
        "currency": home.currency,
        "meters": meters,
        "gas": gas_out,
        "buckets": out_buckets,
    }


# --- live power ----------------------------------------------------------------------------


async def live_power(db: AsyncSession, home_id: uuid.UUID) -> dict | None:
    """Current power per role. A stale sub-meter is shown as missing (not dropped), and makes
    "rest" an estimate instead of hiding the gap."""
    now = datetime.now(UTC)
    parts = []
    total = None
    total_at = None
    missing: list[str] = []
    grid_bound = False
    for role, label in ENERGY_ROLES:
        d = await device_for_role(db, home_id, role)
        if d is None:
            continue
        r = await latest_reading(db, d.id, "power_w")
        fresh = r is not None and r.value is not None and (now - r.observed_at) <= LIVE_STALE
        if role == "grid":
            grid_bound = True
            if fresh:
                total, total_at = r.value, r.observed_at
            continue
        if fresh:
            parts.append(
                {
                    "role": role,
                    "label": label,
                    "watts": r.value,
                    "quality": "measured",
                    "at": r.observed_at,
                    "stale": False,
                }
            )
        else:
            parts.append(
                {
                    "role": role,
                    "label": label,
                    "watts": None,
                    "quality": "missing",
                    "at": r.observed_at if r else None,
                    "stale": True,
                }
            )
            missing.append(label)
    if total is None and not any(p["watts"] is not None for p in parts):
        return None
    note = None
    if total is not None:
        raw_rest = total - sum(p["watts"] or 0 for p in parts)
        quality = "derived"
        if missing:
            quality = "estimated"
            note = f"Onvolledig: geen actuele meting van {', '.join(missing)}; overig bevat dat verbruik."
        elif raw_rest < -REST_TOLERANCE_W:
            quality = "estimated"
            note = "Tussenmeters samen hoger dan P1 (teruglevering of meetmoment); overig op 0 gezet."
        parts.append(
            {
                "role": "rest",
                "label": "Overig",
                "watts": round(max(0.0, raw_rest), 1),
                "quality": quality,
                "at": total_at,
                "stale": False,
                "note": note,
            }
        )
    elif grid_bound:
        note = "Geen actuele meting van de P1-meter."
    times = [p["at"] for p in parts if p["at"] is not None and not p["stale"]]
    return {
        "at": total_at or (max(times) if times else now),
        "total_w": total,
        "complete": total is not None and not missing,
        "note": note,
        "parts": parts,
    }


# --- export --------------------------------------------------------------------------------

EXPORT_SERIES = [
    ("grid", "energy_import_kwh", "kWh"),
    ("grid", "energy_export_kwh", "kWh"),
    ("grid", "energy_import_t1_kwh", "kWh"),
    ("grid", "energy_import_t2_kwh", "kWh"),
    ("grid", en.GAS_METRIC, "m3"),
    ("gas", en.GAS_METRIC, "m3"),
    ("airco", "energy_import_kwh", "kWh"),
    ("boiler", "energy_import_kwh", "kWh"),
]
RAW_EXPORT_METRICS = [*en.COUNTER_METRICS, "power_w"]


def _month_chunks(start: datetime, end: datetime, tz: ZoneInfo):
    cur = start
    while cur < end:
        first = cur.astimezone(tz).date().replace(day=1)
        nxt = datetime.combine((first + timedelta(days=32)).replace(day=1), time(0), tz)
        yield cur, min(nxt, end)
        cur = nxt


async def export_rows(
    db: AsyncSession, home: Home, start: datetime, end: datetime, resolution: str
) -> AsyncIterator[dict]:
    """Rows for the export, streamed in monthly chunks. Hour/day rows come from the aggregates;
    every row is limited to the period in which that meter was bound to its role."""
    tz = ZoneInfo(home.timezone)
    names: dict[uuid.UUID, str] = {}

    async def name(device_id):
        if device_id not in names:
            d = await db.get(Device, device_id)
            names[device_id] = d.name if d else str(device_id)
        return names[device_id]

    if resolution == "raw":
        for role in ("grid", "airco", "boiler", "gas"):
            for bnd in await bindings_for(db, home.id, role):
                lo = max(start, bnd.effective_from)
                hi = min(end, bnd.effective_to) if bnd.effective_to else end
                if hi <= lo:
                    continue
                metrics = RAW_EXPORT_METRICS if role != "gas" else [en.GAS_METRIC]
                q = (
                    select(SensorReading)
                    .where(
                        SensorReading.device_id == bnd.device_id,
                        SensorReading.metric.in_(metrics),
                        SensorReading.observed_at >= lo,
                        SensorReading.observed_at < hi,
                    )
                    .order_by(SensorReading.observed_at)
                    .execution_options(yield_per=2000)
                )
                dev_name = await name(bnd.device_id)
                result = await db.stream_scalars(q)
                async for s in result:
                    yield {
                        "at": s.observed_at,
                        "role": role,
                        "device": dev_name,
                        "metric": s.metric,
                        "value": s.value,
                        "unit": s.unit,
                        "quality": s.quality,
                    }
        return

    ctx = EnergyContext()
    ctx.overrides = await rate_overrides(db)
    unit = "hour" if resolution == "hour" else "day"
    seen_gas_role = bool(await bindings_for(db, home.id, "gas"))
    for c0, c1 in _month_chunks(start, end, tz):
        buckets_template = en.make_buckets(c0.astimezone(tz), c1.astimezone(tz), unit, tz)
        for role, metric, unit_label in EXPORT_SERIES:
            if role == "grid" and metric == en.GAS_METRIC and seen_gas_role:
                continue
            for bnd in await bindings_for(db, home.id, role):
                lo = max(c0, bnd.effective_from)
                hi = min(c1, bnd.effective_to) if bnd.effective_to else c1
                if hi <= lo:
                    continue
                bs = [en.Bucket(b.start, b.end) for b in buckets_template]
                await binding_into(db, ctx, bnd.device_id, role, metric, lo, hi, bs, unit, home.timezone)
                if metric != "energy_import_kwh" and not any(b.covered_s > 0 for b in bs):
                    continue  # this meter has no such counter (e.g. no gas, no T1/T2)
                dev_name = await name(bnd.device_id)
                lo_ts, hi_ts = lo.timestamp(), hi.timestamp()
                for b in bs:
                    if b.end_ts <= lo_ts or b.start_ts >= hi_ts or b.start > ctx.now:
                        continue
                    b.finalize()
                    has = b.covered_s > 0
                    yield {
                        "at": b.start.astimezone(UTC),
                        "role": "gas" if metric == en.GAS_METRIC else role,
                        "device": dev_name,
                        "metric": metric,
                        "value": round(b.kwh, 4) if has else None,
                        "unit": unit_label,
                        "quality": ("measured" if b.complete else "estimated") if has else "missing",
                        "notes": "; ".join(b.notes),
                    }
