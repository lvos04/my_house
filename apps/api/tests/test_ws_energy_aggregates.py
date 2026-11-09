"""Energy stream: plausibility, DST, hourly aggregates, retention, summaries from aggregates,
tariffs, gas, export, live power and the realtime messages (verbeterlijst 7, 19, 20, 21, 35, 36)."""

import csv
import io
import json
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, insert, select

from shc_api import energy as e
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import Alert, AuditLog, Device, DeviceBinding, EnergyHourly, LogicalRole, SensorReading
from shc_api.store import ingest_reading, raise_alert, resolve_alert
from shc_api.worker.aggregates import AggregatesMixin

AMS = ZoneInfo("Europe/Amsterdam")


class W(AggregatesMixin):
    def __init__(self):
        self.settings = get_settings()


async def new_home(admin, name=None) -> str:
    r = await admin.post("/homes", json={"name": name or f"E-{uuid.uuid4().hex[:6]}"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def add_meter(home_id, kind, name, role=None, since=None) -> uuid.UUID:
    async with sessionmaker()() as db:
        d = Device(
            home_id=uuid.UUID(home_id),
            provider="test",
            kind=kind,
            name=name,
            stable_identity=uuid.uuid4().hex,
            online=True,
            last_seen=datetime.now(UTC),
        )
        db.add(d)
        await db.flush()
        if role:
            r = (
                await db.execute(select(LogicalRole).where(LogicalRole.home_id == d.home_id, LogicalRole.key == role))
            ).scalar_one()
            db.add(
                DeviceBinding(
                    role_id=r.id, device_id=d.id, effective_from=since or datetime.now(UTC) - timedelta(days=800)
                )
            )
        await db.commit()
        return d.id


async def put_readings(device_id, metric, points, unit="kWh"):
    rows = [
        {
            "device_id": device_id,
            "metric": metric,
            "value": v,
            "unit": unit,
            "quality": "measured",
            "observed_at": t,
            "event_id": f"t:{metric}:{t.isoformat()}",
        }
        for t, v in points
    ]
    async with sessionmaker()() as db:
        for i in range(0, len(rows), 5000):
            await db.execute(insert(SensorReading), rows[i : i + 5000])
        await db.commit()


async def aggregate_device(device_id):
    w = W()
    end = e.floor_hour(datetime.now(UTC)) + timedelta(hours=1)
    for m in e.COUNTER_METRICS:
        await w.aggregate_series(device_id, m, 17.25, end)


def series(start, n, step_min, v0, inc):
    return [(start + timedelta(minutes=step_min * i), v0 + inc * i) for i in range(n)]


# --- pure logic --------------------------------------------------------------------------


def P(minutes, v):
    return e.CounterPoint(datetime(2026, 11, 8, 10, tzinfo=UTC) + timedelta(minutes=minutes), v)


def test_single_wrong_zero_does_not_create_thousands_of_kwh():
    pts = [P(0, 13779.0), P(1, 13779.02), P(2, 0.0), P(3, 13779.06), P(4, 13779.08)]
    iv = e.clean_intervals(pts, max_rate=17.25)
    assert sum(i.amount for i in iv) == pytest.approx(0.08)
    assert any(e.F_SUSPECT in i.flags for i in iv)


def test_spike_and_negative_values_are_skipped():
    pts = [P(0, 100.0), P(1, 100.01), P(2, 99999.0), P(3, -5.0), P(4, 100.03)]
    iv = e.clean_intervals(pts, max_rate=3.7)
    assert sum(i.amount for i in iv) == pytest.approx(0.03)


def test_reset_only_accepted_when_following_readings_rise_from_new_base():
    pts = [P(0, 500.0), P(10, 500.2), P(20, 0.1), P(30, 0.3), P(40, 0.5)]
    iv = e.clean_intervals(pts, max_rate=17.25)
    assert sum(i.amount for i in iv) == pytest.approx(0.2 + 0.1 + 0.2 + 0.2)
    assert any(e.F_RESET in i.flags for i in iv)
    # same drop but the counter comes back: an outlier, not a reset
    pts = [P(0, 500.0), P(10, 500.2), P(20, 0.1), P(30, 500.4), P(40, 500.6)]
    iv = e.clean_intervals(pts, max_rate=17.25)
    assert sum(i.amount for i in iv) == pytest.approx(0.6)
    assert not any(e.F_RESET in i.flags for i in iv)
    # an unconfirmed drop at the end counts nothing (yet)
    iv = e.clean_intervals([P(0, 500.0), P(10, 500.2), P(20, 0.1)], max_rate=17.25)
    assert sum(i.amount for i in iv) == pytest.approx(0.2)


def test_implausible_jump_over_role_limit_is_suspect():
    # a socket (3.7 kW) can not use 5 kWh in 10 minutes; the readings after it confirm a new base
    pts = [P(0, 10.0), P(10, 10.1), P(20, 15.1), P(30, 15.2), P(40, 15.3), P(50, 15.4)]
    iv = e.clean_intervals(pts, max_rate=3.7)
    assert sum(i.amount for i in iv) == pytest.approx(0.1 + 0.3)
    assert any(e.F_SUSPECT in i.flags for i in iv)
    # the grid (17 kW) may
    assert sum(i.amount for i in e.clean_intervals(pts, max_rate=17.25)) == pytest.approx(5.4)


@pytest.mark.parametrize(("day", "hours"), [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)])
def test_dst_days_are_complete_and_counted_in_real_hours(day, hours):
    s, end, unit = e.period_bounds("day", day, AMS)
    b = e.make_buckets(s, end, unit, AMS)
    assert len(b) == hours
    start = s.astimezone(UTC)
    pts = [e.CounterPoint(start + timedelta(minutes=15 * i), 100 + 0.25 * i) for i in range(hours * 4 + 1)]
    e.distribute(e.clean_intervals(pts, 17.25), b)
    for x in b:
        x.finalize()
    assert all(x.complete for x in b)
    assert sum(x.kwh for x in b) == pytest.approx(hours * 1.0)
    day_bucket = e.make_buckets(s, end, "day", AMS)[0]
    assert day_bucket.duration_s == hours * 3600


def test_hourly_flags_incomplete_and_gap():
    t0 = datetime(2026, 11, 8, 10, tzinfo=UTC)
    pts = [e.CounterPoint(t0 + timedelta(minutes=m), 1 + m / 60) for m in (0, 10, 20, 30, 40, 50, 60, 180, 190)]
    rows = e.hourly(pts, t0, t0 + timedelta(hours=4), 17.25)
    by = {r.hour_start.hour: r for r in rows}
    assert by[10].flags == [] and by[10].kwh == pytest.approx(1.0) and by[10].samples == 6
    assert e.F_GAP in by[11].flags and e.F_INCOMPLETE not in by[11].flags  # covered, by a long interval
    assert sum(r.kwh for r in rows) == pytest.approx(190 / 60)


def test_tariff_table_and_t1_t2_price():
    t = e.TariffTable(
        [
            e.TariffPeriod(date(2026, 1, 1), 0.30),
            e.TariffPeriod(date(2026, 6, 15), 0.20, import_t1_per_kwh=0.25, import_t2_per_kwh=0.15),
        ]
    )
    assert t.at(date(2025, 12, 31)) is None
    assert t.at(date(2026, 6, 14)).import_per_kwh == 0.30
    p = t.at(date(2026, 6, 15))
    assert e.import_price(p, 1.0, 3.0) == pytest.approx((0.25 + 0.45) / 4)
    assert e.import_price(p) == 0.20


# --- ingest / alerts ---------------------------------------------------------------------


async def test_negative_and_nan_counters_rejected_at_ingest(admin):
    hid = await new_home(admin)
    d = await add_meter(hid, "p1_meter", "P1", "grid")
    now = datetime.now(UTC)
    async with sessionmaker()() as db:
        assert await ingest_reading(db, d, "energy_import_kwh", -1.0, "kWh", now) is False
        assert await ingest_reading(db, d, "energy_import_kwh", float("nan"), "kWh", now) is False
        assert await ingest_reading(db, d, "power_w", -350.0, "W", now) is True  # export: negative power is fine
        assert await ingest_reading(db, d, "energy_import_kwh", 12.5, "kWh", now) is True
        await db.commit()


async def test_resolve_alert_publishes_alert_resolved(admin, monkeypatch):
    from shc_api import events

    hid = uuid.UUID(await new_home(admin))
    sent = []
    real = events.publish

    async def spy(db, type_, home_id, data):
        sent.append((type_, home_id, data))
        await real(db, type_, home_id, data)

    monkeypatch.setattr(events, "publish", spy)
    async with sessionmaker()() as db:
        a = await raise_alert(db, hid, f"test:{hid}", "warn", "Test", "x", "test")
        await db.commit()
        resolved = await resolve_alert(db, f"test:{hid}")
        await db.commit()
    assert [r.id for r in resolved] == [a.id]
    assert ("alert.resolved", hid, {"id": a.id}) in sent
    async with sessionmaker()() as db:
        assert (await db.get(Alert, a.id)).resolved_at is not None


# --- aggregation, retention, summaries ------------------------------------------------------


async def test_aggregates_match_raw_and_summaries_read_from_aggregates(admin):
    hid = await new_home(admin)
    grid = await add_meter(hid, "p1_meter", "P1", "grid")
    airco = await add_meter(hid, "kwh_meter", "Airco", "airco")
    start = datetime(2026, 10, 24, 22, tzinfo=UTC)  # local midnight 25 Oct (CEST), the 25-hour day
    n = 4 * 24 * 3  # three days at 15 minutes
    await put_readings(grid, "energy_import_kwh", series(start, n + 1, 15, 1000.0, 0.25))
    await put_readings(grid, "energy_export_kwh", series(start, n + 1, 15, 50.0, 0.0))
    await put_readings(airco, "energy_import_kwh", series(start, n + 1, 15, 10.0, 0.05))

    before = (await admin.get(f"/homes/{hid}/energy/summary", params={"period": "day", "date": "2026-10-25"})).json()
    assert len(before["buckets"]) == 25 and all(b["complete"] for b in before["buckets"])
    assert before["total_kwh"] == pytest.approx(25.0)

    w = W()
    stats = await w.aggregate_round()
    assert stats["hours"] >= 72
    again = await w.aggregate_round()  # idempotent: same rows, nothing duplicated
    async with sessionmaker()() as db:
        rows = (await db.execute(select(EnergyHourly).where(EnergyHourly.device_id == grid))).scalars().all()
        imp = [r for r in rows if r.metric == "energy_import_kwh" and r.hour_start < start + timedelta(hours=72)]
        assert len(imp) == 72 and sum(r.kwh for r in imp) == pytest.approx(72.0)
        assert all(r.samples == 4 and r.flags == [] for r in imp)
    assert again["hours"] <= stats["hours"]

    # Remove the raw readings: the summaries must come from the aggregates now.
    async with sessionmaker()() as db:
        await db.execute(SensorReading.__table__.delete().where(SensorReading.device_id.in_([grid, airco])))
        await db.commit()
    after = (await admin.get(f"/homes/{hid}/energy/summary", params={"period": "day", "date": "2026-10-25"})).json()
    assert after["total_kwh"] == pytest.approx(25.0)
    assert [b["values"]["grid"] for b in after["buckets"]] == [b["values"]["grid"] for b in before["buckets"]]
    meters = {m["role"]: m for m in after["meters"]}
    assert meters["airco"]["kwh"] == pytest.approx(5.0) and meters["rest"]["kwh"] == pytest.approx(20.0)
    assert meters["rest"]["quality"] == "derived" and meters["grid"]["quality"] == "measured"
    month = (await admin.get(f"/homes/{hid}/energy/summary", params={"period": "month", "date": "2026-10-25"})).json()
    by_day = {b["start"][:10]: b for b in month["buckets"]}
    assert by_day["2026-10-25"]["values"]["grid"] == pytest.approx(25.0) and by_day["2026-10-25"]["complete"]
    assert by_day["2026-10-26"]["values"]["grid"] == pytest.approx(24.0)
    assert month["total_kwh"] == pytest.approx(72.0)
    year = (await admin.get(f"/homes/{hid}/energy/summary", params={"period": "year", "date": "2026-10-25"})).json()
    assert len(year["buckets"]) == 12
    assert year["buckets"][9]["values"]["grid"] == pytest.approx(72.0)


async def test_year_summary_from_aggregates_is_fast(admin):
    hid = await new_home(admin)
    grid = await add_meter(hid, "p1_meter", "P1", "grid")
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [
        {"device_id": grid, "metric": "energy_import_kwh", "hour_start": t0 + timedelta(hours=h), "kwh": 0.5}
        for h in range(24 * 365)
    ]
    async with sessionmaker()() as db:
        await db.execute(insert(EnergyHourly), rows)
        await db.commit()
    t = time.monotonic()
    r = await admin.get(f"/homes/{hid}/energy/summary", params={"period": "year", "date": "2025-06-01"})
    elapsed = time.monotonic() - t
    assert r.status_code == 200
    assert r.json()["total_kwh"] == pytest.approx(0.5 * 24 * 364 + 0.5 * 23)  # 2026-01-01 00:00 local = 23:00Z
    assert elapsed < 3.0


async def test_suspect_reading_is_flagged_and_not_counted(admin):
    hid = await new_home(admin)
    sock = await add_meter(hid, "energy_socket", "Boiler", "boiler")
    grid = await add_meter(hid, "p1_meter", "P1", "grid")
    start = datetime(2026, 9, 1, 22, tzinfo=UTC)  # local midnight 2 Sept
    pts = series(start, 97, 15, 200.0, 0.1)
    pts[40] = (pts[40][0], 13000.0)  # one wrong reading
    await put_readings(sock, "energy_import_kwh", pts)
    await put_readings(grid, "energy_import_kwh", series(start, 97, 15, 5000.0, 0.5))
    s = (await admin.get(f"/homes/{hid}/energy/summary", params={"period": "day", "date": "2026-09-02"})).json()
    m = {x["role"]: x for x in s["meters"]}
    assert m["boiler"]["kwh"] == pytest.approx(9.6)
    assert m["boiler"]["quality"] == "estimated"
    assert e.NOTE_SUSPECT in m["boiler"]["notes"]
    await aggregate_device(sock)
    async with sessionmaker()() as db:
        flagged = (
            await db.execute(
                select(func.count())
                .select_from(EnergyHourly)
                .where(EnergyHourly.device_id == sock, EnergyHourly.flags.contains(["suspect"]))
            )
        ).scalar_one()
    assert flagged >= 1


async def test_usage_across_an_outage_over_midnight_is_not_lost(admin):
    hid = await new_home(admin)
    grid = await add_meter(hid, "p1_meter", "P1", "grid")
    d1 = datetime(2026, 8, 31, 22, tzinfo=UTC)  # local midnight 1 Sept
    first = series(d1, 4 * 20 + 1, 15, 100.0, 0.25)  # until 20:00 local
    gap_end = d1 + timedelta(hours=27)  # 03:00 local on 2 Sept
    second = series(gap_end, 4 * 21 + 1, 15, first[-1][1] + 7.0, 0.25)
    await put_readings(grid, "energy_import_kwh", first + second)
    day1 = (await admin.get(f"/homes/{hid}/energy/summary", params={"period": "day", "date": "2026-09-01"})).json()
    day2 = (await admin.get(f"/homes/{hid}/energy/summary", params={"period": "day", "date": "2026-09-02"})).json()
    # 7 kWh over the 7-hour outage: 4 h on day 1, 3 h on day 2
    assert day1["total_kwh"] == pytest.approx(20.0 + 4.0)
    assert day2["total_kwh"] == pytest.approx(3.0 + 21.0)
    assert day1["meters"][0]["quality"] == "estimated"
    assert not all(b["complete"] for b in day1["buckets"])


async def test_retention_keeps_unaggregated_counters(admin):
    hid = await new_home(admin)
    agg = await add_meter(hid, "p1_meter", "P1", "grid")
    fresh = await add_meter(hid, "kwh_meter", "Nieuw")
    old = datetime.now(UTC) - timedelta(days=400)
    await put_readings(agg, "energy_import_kwh", series(old, 50, 15, 10.0, 0.1))
    await put_readings(agg, "power_w", series(old, 50, 15, 100.0, 0.0), unit="W")
    await put_readings(fresh, "energy_import_kwh", series(old, 50, 15, 10.0, 0.1))
    w = W()
    async with sessionmaker()() as db:
        await db.execute(
            insert(EnergyHourly),
            [
                {
                    "device_id": agg,
                    "metric": "energy_import_kwh",
                    "hour_start": e.floor_hour(datetime.now(UTC)),
                    "kwh": 0,
                }
            ],
        )
        await db.commit()
    out = await w.retention_round()
    assert out["power"] >= 50 and out["counters"] >= 50
    async with sessionmaker()() as db:

        async def count(d, m):
            q = (
                select(func.count())
                .select_from(SensorReading)
                .where(SensorReading.device_id == d, SensorReading.metric == m)
            )
            return (await db.execute(q)).scalar_one()

        assert await count(agg, "energy_import_kwh") == 0
        assert await count(agg, "power_w") == 0
        assert await count(fresh, "energy_import_kwh") == 50  # never aggregated -> kept


# --- tariffs ----------------------------------------------------------------------------------


async def test_tariff_optional_fields_conflict_update_delete_and_audit(admin):
    hid = await new_home(admin)
    r = await admin.post(
        f"/homes/{hid}/tariffs",
        json={"valid_from": "2026-01-01", "import_per_kwh": 0.3, "fixed_per_day": "", "export_per_kwh": None},
    )
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["fixed_per_day"] == 0 and t["export_per_kwh"] == 0 and t["gas_per_m3"] is None
    r = await admin.post(f"/homes/{hid}/tariffs", json={"valid_from": "2026-01-01", "import_per_kwh": 0.2})
    assert r.status_code == 409 and "al een tarief" in r.json()["detail"]["message"]
    r = await admin.post(
        f"/homes/{hid}/tariffs", json={"valid_from": "2026-02-01", "import_per_kwh": 0.2, "import_t1_per_kwh": 0.3}
    )
    assert r.status_code == 422 and "T2" in r.json()["detail"]["message"]
    r = await admin.post(f"/homes/{hid}/tariffs", json={"valid_from": "2026-02-01", "import_per_kwh": 9})
    assert r.status_code == 422 and "Stroomprijs" in r.json()["detail"]["message"]
    r = await admin.put(
        f"/tariffs/{t['id']}",
        {"valid_from": "2026-01-01", "import_per_kwh": 0.28, "fixed_per_day": 1.1, "gas_per_m3": 1.35},
    )
    assert r.status_code == 200 and r.json()["import_per_kwh"] == 0.28 and r.json()["gas_per_m3"] == 1.35
    lst = (await admin.get(f"/homes/{hid}/tariffs")).json()
    assert len(lst) == 1 and lst[0]["fixed_per_day"] == 1.1
    assert (await admin.delete(f"/tariffs/{t['id']}")).status_code == 204
    assert (await admin.get(f"/homes/{hid}/tariffs")).json() == []
    async with sessionmaker()() as db:
        actions = (
            (
                await db.execute(
                    select(AuditLog.action).where(AuditLog.action.like("tariff_%"), AuditLog.target == "2026-01-01")
                )
            )
            .scalars()
            .all()
        )
    assert {"tariff_added", "tariff_updated", "tariff_deleted"} <= set(actions)


async def test_costs_split_tariffs_fixed_costs_partial_coverage_t1_t2_and_gas(admin):
    hid = await new_home(admin)
    grid = await add_meter(hid, "p1_meter", "P1", "grid")
    start = datetime(2026, 4, 30, 22, tzinfo=UTC)  # 1 May 00:00 local (CEST)
    n = 4 * 24 * 31
    await put_readings(grid, "energy_import_kwh", series(start, n + 1, 15, 0.0, 0.25))  # 24 kWh/day
    await put_readings(grid, "energy_import_t1_kwh", series(start, n + 1, 15, 0.0, 0.1875))  # 3/4 at T1
    await put_readings(grid, "energy_import_t2_kwh", series(start, n + 1, 15, 0.0, 0.0625))
    await put_readings(grid, "gas_m3", series(start, 24 * 31 + 1, 60, 100.0, 0.1), unit="m3")
    await aggregate_device(grid)
    # no tariff before 11 May: partial cost with coverage, not null
    await admin.post(
        f"/homes/{hid}/tariffs", json={"valid_from": "2026-05-11", "import_per_kwh": 0.25, "fixed_per_day": 1.0}
    )
    s = (await admin.get(f"/homes/{hid}/energy/summary", params={"period": "month", "date": "2026-05-01"})).json()
    assert s["total_kwh"] == pytest.approx(24 * 31)
    assert s["cost_coverage"] == pytest.approx(21 / 31, abs=0.002)
    assert s["fixed_cost"] == pytest.approx(21.0) and s["fixed_days"] == 21
    assert s["cost"] == pytest.approx(24 * 21 * 0.25 + 21.0)
    assert "gedeeltelijk" in s["cost_note"]
    # a change on 21 May with T1/T2 prices: the year view splits the month at the change
    await admin.post(
        f"/homes/{hid}/tariffs",
        json={
            "valid_from": "2026-05-21",
            "import_per_kwh": 0.30,
            "import_t1_per_kwh": 0.40,
            "import_t2_per_kwh": 0.20,
            "fixed_per_day": 0.5,
            "gas_per_m3": 1.5,
        },
    )
    y = (await admin.get(f"/homes/{hid}/energy/summary", params={"period": "year", "date": "2026-05-01"})).json()
    may = y["buckets"][4]
    t1t2 = 0.75 * 0.40 + 0.25 * 0.20
    expected_var = 24 * 10 * 0.25 + 24 * 11 * t1t2
    assert may["cost"] == pytest.approx(expected_var, abs=0.05)
    assert y["fixed_cost"] == pytest.approx(10 * 1.0 + 11 * 0.5 + (y["fixed_days"] - 21) * 0.5)
    assert y["gas"]["m3"] == pytest.approx(0.1 * 24 * 31, abs=0.01)
    assert y["gas"]["cost"] == pytest.approx(0.1 * 24 * 11 * 1.5, abs=0.05)
    assert may["gas_m3"] == pytest.approx(0.1 * 24 * 31, abs=0.01)


# --- export -------------------------------------------------------------------------------------


async def test_export_streams_hourly_aggregates_and_respects_bindings(admin):
    hid = await new_home(admin)
    old = await add_meter(hid, "kwh_meter", "Airco oud", "airco", since=datetime(2026, 1, 1, tzinfo=UTC))
    start = datetime(2026, 7, 31, 22, tzinfo=UTC)  # 1 Aug local
    switch = start + timedelta(hours=12)
    async with sessionmaker()() as db:
        b = (await db.execute(select(DeviceBinding).where(DeviceBinding.device_id == old))).scalar_one()
        b.effective_to = switch
        await db.commit()
    new = await add_meter(hid, "kwh_meter", "Airco nieuw")
    async with sessionmaker()() as db:
        role = (
            await db.execute(
                select(LogicalRole).where(LogicalRole.home_id == uuid.UUID(hid), LogicalRole.key == "airco")
            )
        ).scalar_one()
        db.add(DeviceBinding(role_id=role.id, device_id=new, effective_from=switch))
        await db.commit()
    # the old meter keeps reporting after it was unbound: those rows must not be exported as airco
    await put_readings(old, "energy_import_kwh", series(start, 97, 15, 50.0, 0.05))
    await put_readings(new, "energy_import_kwh", series(switch, 49, 15, 0.0, 0.05))
    r = await admin.get(f"/homes/{hid}/energy/export", params={"from": "2026-08-01", "to": "2026-08-01"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text), delimiter=";"))
    assert rows[0][:6] == ["tijd_utc", "tijd_lokaal", "rol", "apparaat", "meting", "waarde"]
    airco = [x for x in rows[1:] if x[2] == "airco"]
    assert len(airco) == 24
    assert {x[3] for x in airco[:12]} == {"Airco oud"} and {x[3] for x in airco[12:]} == {"Airco nieuw"}
    assert sum(float(x[5].replace(",", ".")) for x in airco) == pytest.approx(4.8)
    j = await admin.get(
        f"/homes/{hid}/energy/export",
        params={"from": "2026-08-01", "to": "2026-08-01", "format": "json", "resolution": "day"},
    )
    body = json.loads(j.text)
    assert body["schema"] == "shc.energy.v2" and body["resolution"] == "day"
    assert sum(x["value"] for x in body["rows"] if x["role"] == "airco") == pytest.approx(4.8)
    raw = await admin.get(
        f"/homes/{hid}/energy/export", params={"from": "2026-08-01", "to": "2026-08-01", "resolution": "raw"}
    )
    raw_rows = list(csv.reader(io.StringIO(raw.text), delimiter=";"))[1:]
    assert len([x for x in raw_rows if x[3] == "Airco oud"]) == 48  # only while bound (until 12:00 local)
    too_long = await admin.get(
        f"/homes/{hid}/energy/export", params={"from": "2026-01-01", "to": "2026-03-01", "resolution": "raw"}
    )
    assert too_long.status_code == 422 and "31 dagen" in too_long.json()["detail"]["message"]


async def test_export_missing_values_are_empty_cells(admin):
    hid = await new_home(admin)
    grid = await add_meter(hid, "p1_meter", "P1", "grid")
    start = datetime(2026, 6, 30, 22, tzinfo=UTC)
    await put_readings(grid, "energy_import_kwh", series(start, 5, 15, 1.0, 0.1))
    r = await admin.get(f"/homes/{hid}/energy/export", params={"from": "2026-07-01", "to": "2026-07-01"})
    rows = [x for x in list(csv.reader(io.StringIO(r.text), delimiter=";"))[1:] if x[4] == "energy_import_kwh"]
    assert len(rows) == 24
    assert rows[0][5] != "" and rows[5][5] == "" and rows[5][7] == "missing"


# --- live power -------------------------------------------------------------------------------


async def test_live_power_shows_stale_submeter_and_live_round_publishes(admin, monkeypatch):
    from shc_api.worker import aggregates

    hid = await new_home(admin)
    grid = await add_meter(hid, "p1_meter", "P1", "grid")
    boiler = await add_meter(hid, "energy_socket", "Boiler", "boiler")
    now = datetime.now(UTC)
    await put_readings(grid, "power_w", [(now - timedelta(seconds=5), 1500.0)], unit="W")
    await put_readings(boiler, "power_w", [(now - timedelta(minutes=30), 2000.0)], unit="W")
    live = (await admin.get(f"/homes/{hid}/energy/live")).json()
    parts = {p["role"]: p for p in live["parts"]}
    assert parts["boiler"]["watts"] is None and parts["boiler"]["stale"] is True
    assert parts["rest"]["quality"] == "estimated" and "Boiler" in parts["rest"]["note"]
    assert live["complete"] is False and live["total_w"] == 1500.0

    sent = []

    async def spy(db, type_, home_id, data):
        sent.append((type_, home_id))

    monkeypatch.setattr(aggregates.events, "publish", spy)
    w = W()
    await w.live_round()
    assert ("energy.live", uuid.UUID(hid)) in sent
    n = len(sent)
    await w.live_round()  # nothing newer: no message
    assert len(sent) == n
    await put_readings(grid, "power_w", [(datetime.now(UTC), 1600.0)], unit="W")
    await w.live_round()  # newer reading, but within 5 s of the last message
    assert len(sent) == n
    w._live_state[uuid.UUID(hid)] = (w._live_state[uuid.UUID(hid)][0], time.monotonic() - 10)
    await w.live_round()
    assert len(sent) == n + 1


async def test_energy_limits_setting(admin):
    r = await admin.put("/energy/limits", {"airco": 3.5})
    assert r.status_code == 200 and r.json()["airco"] == 3.5 and r.json()["grid"] == 17.25
    assert (await admin.put("/energy/limits", {"airco": 0})).status_code == 422
    assert (await admin.put("/energy/limits", {"airco": None})).json()["airco"] == e.ROLE_MAX_KW["airco"]
