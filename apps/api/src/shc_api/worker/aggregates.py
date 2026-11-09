"""Energy aggregation, data retention and live power (verbeterlijst 19, 36; contract C2).

- ``aggregate_round`` (every 300 s): folds raw counter readings into ``energy_hourly`` per
  device/metric/hour. Each series continues from its frontier (the hour after its last aggregated
  hour) and always recomputes the last ~3 hours, so late readings are picked up. The first run
  backfills from the oldest raw reading forward in chunks of a week, with a time budget per round,
  committing per chunk. Rows in the recomputed window are replaced (idempotent).
- ``retention_round`` (every 3600 s): deletes raw power readings older than the power retention
  and raw counter readings older than the counter retention, in batches — but never counter
  readings that are not yet aggregated. Also prunes old zone log lines and the push-event dedupe
  ledger. The periods are set in the app (Setting "retention", GET/PUT /system/retention); the
  SHC_RETENTION_*_DAYS environment values are the defaults.
- ``live_round`` (every 5 s): publishes ``energy.live`` (views.energy.live_power) for a home when
  a newer power reading exists, at most once per 5 s per home.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert

from .. import energy as en
from .. import events
from ..config import Settings, get_settings
from ..db import sessionmaker
from ..models import Device, DeviceBinding, EnergyHourly, Home, LogicalRole, SensorReading
from ..store import get_setting
from ..views.energy import ENERGY_ROLES, device_rate, live_power, rate_overrides

log = logging.getLogger("shc.worker")

RECOMPUTE_HOURS = 3
CHUNK = timedelta(days=7)
AGGREGATE_BUDGET_S = 30.0
DELETE_BATCH = 10_000
DELETE_MAX_BATCHES = 200  # per table and round; the rest follows next round
LIVE_MIN_INTERVAL_S = 5.0
PROCESSED_EVENTS_DAYS = 7
COUNTER_SAFETY = timedelta(days=1)  # keep this much raw data behind the aggregation frontier
# Device kinds that never report energy/gas counters (skipped to keep the round cheap with many lamps).
NO_COUNTER_KINDS = ("light", "light_group", "scene", "button", "hue_device", "hue_bridge", "ac_unit", "zigbee_bridge")


# Retention periods (days) set in the app: Setting "retention" = {power_days, counter_days, log_days}.
RETENTION_KEY = "retention"
RETENTION_FIELDS = ("power_days", "counter_days", "log_days")
# Lowest values the app accepts: a week of power detail, a quarter of raw counters (the hourly totals
# are kept forever and un-aggregated counters are never deleted), a week of zone log.
RETENTION_MIN = {"power_days": 7, "counter_days": 90, "log_days": 7}
RETENTION_MAX_DAYS = 3650


def retention_defaults(s: Settings | None = None) -> dict[str, int]:
    """The environment values (SHC_RETENTION_*_DAYS): used when nothing is set in the app."""
    s = s or get_settings()
    return {
        "power_days": s.retention_power_days,
        "counter_days": s.retention_counter_days,
        "log_days": s.retention_log_days,
    }


async def load_retention(db, s: Settings | None = None) -> dict:
    """Effective periods: ``{power_days, counter_days, log_days, source: {field: "app"|"env"}}``.
    A stored value outside the accepted range is ignored (the default applies)."""
    defaults = retention_defaults(s)
    stored = await get_setting(db, RETENTION_KEY, {}) or {}
    out: dict = {"source": {}}
    for k in RETENTION_FIELDS:
        v = stored.get(k) if isinstance(stored, dict) else None
        if isinstance(v, int) and not isinstance(v, bool) and RETENTION_MIN[k] <= v <= RETENTION_MAX_DAYS:
            out[k], out["source"][k] = v, "app"
        else:
            out[k], out["source"][k] = max(1, int(defaults[k])), "env"
    return out


class AggregatesMixin:
    # Provided by core.Worker.
    settings: Settings

    # --- aggregation ------------------------------------------------------------------------

    async def aggregate_round(self) -> dict[str, int]:
        """One pass over all counter series; returns {"series": n, "hours": n} for logging/tests."""
        deadline = time.monotonic() + AGGREGATE_BUDGET_S
        now = datetime.now(UTC)
        end = en.floor_hour(now) + timedelta(hours=1)  # include the current (partial) hour
        stats = {"series": 0, "hours": 0}
        async with sessionmaker()() as db:
            overrides = await rate_overrides(db)
            devices = (await db.execute(select(Device).where(Device.kind.not_in(NO_COUNTER_KINDS)))).scalars().all()
            roles = await _device_roles(db)
        for d in devices:
            for metric in en.COUNTER_METRICS:
                if time.monotonic() > deadline:
                    log.info("Energie-aggregatie: tijdbudget op, rest volgt in de volgende ronde")
                    return stats
                rate = device_rate(metric, roles.get(d.id), d, overrides)
                n = await self.aggregate_series(d.id, metric, rate, end, deadline)
                if n:
                    stats["series"] += 1
                    stats["hours"] += n
        return stats

    async def aggregate_series(
        self, device_id: uuid.UUID, metric: str, rate: float, end: datetime, deadline: float | None = None
    ) -> int:
        async with sessionmaker()() as db:
            last = (
                await db.execute(
                    select(func.max(EnergyHourly.hour_start)).where(
                        EnergyHourly.device_id == device_id, EnergyHourly.metric == metric
                    )
                )
            ).scalar_one_or_none()
            if last is None:
                first = (
                    await db.execute(
                        select(func.min(SensorReading.observed_at)).where(
                            SensorReading.device_id == device_id, SensorReading.metric == metric
                        )
                    )
                ).scalar_one_or_none()
                if first is None:
                    return 0
                start = en.floor_hour(first)
            else:
                start = min(last + timedelta(hours=1), en.floor_hour(end) - timedelta(hours=RECOMPUTE_HOURS))
        written = 0
        cur = start
        while cur < end:
            nxt = await _chunk_end(device_id, metric, cur, end)
            written += await self._aggregate_chunk(device_id, metric, rate, cur, nxt)
            cur = nxt
            if deadline is not None and time.monotonic() > deadline:
                break
            await asyncio.sleep(0)
        return written

    async def _aggregate_chunk(
        self, device_id: uuid.UUID, metric: str, rate: float, start: datetime, end: datetime
    ) -> int:
        from ..views.energy import raw_points

        async with sessionmaker()() as db:
            points = await raw_points(db, device_id, metric, start, end)
            if len(points) < 2:
                return 0  # nothing to distribute (e.g. a meter that is no longer reporting)
            gap = en.max_gap_for(metric)
            if len(points) > 2000:
                rows = await asyncio.to_thread(en.hourly, points, start, end, rate, gap)
            else:
                rows = en.hourly(points, start, end, rate, gap)
            await db.execute(
                delete(EnergyHourly).where(
                    EnergyHourly.device_id == device_id,
                    EnergyHourly.metric == metric,
                    EnergyHourly.hour_start >= start,
                    EnergyHourly.hour_start < end,
                )
            )
            if rows:
                now = datetime.now(UTC)
                values = [
                    {
                        "device_id": device_id,
                        "metric": metric,
                        "hour_start": r.hour_start,
                        "kwh": r.kwh,
                        "first_value": r.first_value,
                        "last_value": r.last_value,
                        "samples": r.samples,
                        "flags": r.flags,
                        "updated_at": now,
                    }
                    for r in rows
                ]
                for i in range(0, len(values), 1000):
                    stmt = insert(EnergyHourly).values(values[i : i + 1000])
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["device_id", "metric", "hour_start"],
                        set_={
                            c: stmt.excluded[c]
                            for c in ("kwh", "first_value", "last_value", "samples", "flags", "updated_at")
                        },
                    )
                    await db.execute(stmt)
            await db.commit()
        return len(rows)

    # --- retention ----------------------------------------------------------------------------

    async def retention_round(self) -> dict[str, int]:
        async with sessionmaker()() as db:
            keep = await load_retention(db, self.settings)
        now = datetime.now(UTC)
        out = {"power": 0, "counters": 0, "zone_log": 0, "processed_events": 0}
        power_cut = now - timedelta(days=keep["power_days"])
        out["power"] = await _batched_delete(
            "DELETE FROM sensor_readings WHERE id IN (SELECT id FROM sensor_readings "
            "WHERE metric = ANY(:m) AND observed_at < :cut LIMIT :n)",
            {"m": list(en.POWER_METRICS), "cut": power_cut},
        )
        counter_cut = now - timedelta(days=keep["counter_days"])
        async with sessionmaker()() as db:
            fronts = (
                await db.execute(
                    select(EnergyHourly.device_id, EnergyHourly.metric, func.max(EnergyHourly.hour_start)).group_by(
                        EnergyHourly.device_id, EnergyHourly.metric
                    )
                )
            ).all()
        for device_id, metric, last in fronts:
            # never delete raw counters that are not yet aggregated (plus a safety margin)
            cut = min(counter_cut, last + timedelta(hours=1) - COUNTER_SAFETY)
            out["counters"] += await _batched_delete(
                "DELETE FROM sensor_readings WHERE id IN (SELECT id FROM sensor_readings "
                "WHERE device_id = :d AND metric = :mt AND observed_at < :cut LIMIT :n)",
                {"d": device_id, "mt": metric, "cut": cut},
            )
        log_cut = now - timedelta(days=keep["log_days"])
        out["zone_log"] = await _batched_delete(
            "DELETE FROM zone_log WHERE id IN (SELECT id FROM zone_log WHERE at < :cut LIMIT :n)", {"cut": log_cut}
        )
        out["processed_events"] = await _batched_delete(
            "DELETE FROM processed_events WHERE event_key IN "
            "(SELECT event_key FROM processed_events WHERE at < :cut LIMIT :n)",
            {"cut": now - timedelta(days=PROCESSED_EVENTS_DAYS)},
        )
        if any(out.values()):
            log.info("Opruimen oude gegevens: %s", out)
        return out

    # --- live power -----------------------------------------------------------------------------

    async def live_round(self) -> int:
        """Publish energy.live per home when there is a newer power reading; returns # published."""
        state: dict[uuid.UUID, tuple[datetime, float]] = getattr(self, "_live_state", None) or {}
        self._live_state = state
        published = 0
        async with sessionmaker()() as db:
            homes = (await db.execute(select(Home.id).where(Home.active_to.is_(None)))).scalars().all()
            for home_id in homes:
                ids = (
                    (
                        await db.execute(
                            select(DeviceBinding.device_id)
                            .join(LogicalRole, LogicalRole.id == DeviceBinding.role_id)
                            .where(
                                LogicalRole.home_id == home_id,
                                LogicalRole.key.in_([r for r, _ in ENERGY_ROLES]),
                                DeviceBinding.effective_to.is_(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                newest = None
                for did in ids:
                    t = (
                        await db.execute(
                            select(func.max(SensorReading.observed_at)).where(
                                SensorReading.device_id == did, SensorReading.metric == "power_w"
                            )
                        )
                    ).scalar_one_or_none()
                    if t and (newest is None or t > newest):
                        newest = t
                if newest is None:
                    continue
                prev = state.get(home_id)
                mono = time.monotonic()
                if prev and (newest <= prev[0] or mono - prev[1] < LIVE_MIN_INTERVAL_S):
                    continue
                data = await live_power(db, home_id)
                if data is None:
                    continue
                await events.publish(db, "energy.live", home_id, data)
                state[home_id] = (newest, mono)
                published += 1
            await db.commit()
        return published


async def _chunk_end(device_id: uuid.UUID, metric: str, cur: datetime, end: datetime) -> datetime:
    """A week of data per chunk; empty stretches (meter offline or removed) are skipped in one chunk."""
    nxt = min(cur + CHUNK, end)
    async with sessionmaker()() as db:
        base = select(func.min(SensorReading.observed_at)).where(
            SensorReading.device_id == device_id, SensorReading.metric == metric
        )
        inside = (
            await db.execute(base.where(SensorReading.observed_at >= cur, SensorReading.observed_at < nxt))
        ).scalar_one_or_none()
        if inside is not None:
            return nxt
        after = (await db.execute(base.where(SensorReading.observed_at >= nxt))).scalar_one_or_none()
    if after is None:
        return end
    return min(end, en.floor_hour(after) + timedelta(hours=1))


async def _device_roles(db) -> dict[uuid.UUID, str]:
    """Role per device (current binding preferred, otherwise the most recent one)."""
    rows = (
        await db.execute(
            select(DeviceBinding.device_id, LogicalRole.key, DeviceBinding.effective_to)
            .join(LogicalRole, LogicalRole.id == DeviceBinding.role_id)
            .order_by(DeviceBinding.effective_from)
        )
    ).all()
    out: dict[uuid.UUID, str] = {}
    current: set[uuid.UUID] = set()
    for device_id, key, eff_to in rows:
        if device_id in current:
            continue
        out[device_id] = key
        if eff_to is None:
            current.add(device_id)
    return out


async def _batched_delete(sql: str, params: dict) -> int:
    total = 0
    stmt = text(sql)
    for _ in range(DELETE_MAX_BATCHES):
        async with sessionmaker()() as db:
            res = await db.execute(stmt, {**params, "n": DELETE_BATCH})
            await db.commit()
        n = res.rowcount or 0
        total += n
        if n < DELETE_BATCH:
            break
        await asyncio.sleep(0.05)
    return total
