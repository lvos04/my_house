"""Backend fixes: an interrupted automatic backup counts as a failed attempt, retention of the other raw
sensor readings, the home's time zone for HomeWizard, and alerts carry their key (title follows)."""

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from shc_api import adaptive as ad
from shc_api import backup as bk
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import Alert, BackupRecord, Integration, SensorReading
from shc_api.store import alert_view, get_setting, raise_alert, resolve_alert, set_setting
from shc_api.worker import aggregates as agg
from shc_api.worker import backup_job as bj
from shc_api.worker.core import fingerprint

from .test_ws_backup_format import PW, temp_db
from .ws_devices_support import add_device, add_integration, make_worker, new_home

AMS = ZoneInfo("Europe/Amsterdam")


@contextlib.asynccontextmanager
async def settings_restored(*keys):
    async with sessionmaker()() as db:
        before = {k: await get_setting(db, k) for k in keys}
    try:
        yield
    finally:
        async with sessionmaker()() as db:
            for k, v in before.items():
                await set_setting(db, k, v)
            await resolve_alert(db, bj.ALERT_KEY)
            await db.commit()


def T(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(UTC)


# --- 1. interrupted automatic backup ------------------------------------------------------------


def test_backup_due_counts_running_since_without_result_as_failed_attempt():
    since = T("2026-11-08T03:00:00+01:00")
    state = {"running_since": since.isoformat(), "running_kind": "full", "last_ok": "x"}
    # The 03:00 attempt never reported back: back-off as after one failure (1 h), not "due right away".
    assert bj.interrupted_since(state) == since
    assert not bj.backup_due(since + timedelta(minutes=30), 3, AMS, None, state)
    assert bj.backup_due(since + timedelta(minutes=61), 3, AMS, None, state)
    assert bj.next_due(since + timedelta(minutes=30), 3, AMS, None, state) == since + timedelta(hours=1)
    eff = bj.effective_state(state)
    assert eff == {
        "last_attempt": since.isoformat(),
        "last_error": "Back-up onderbroken (worker herstart)",
        "failures": 1,
        "last_ok": "x",
    }
    # Earlier failures count on: third failure -> 4 h.
    st3 = {**state, "failures": 2, "last_attempt": (since - timedelta(hours=2)).isoformat()}
    assert bj.retry_after(bj.effective_state(st3)) == since + timedelta(hours=4)
    # A newer automatic backup than the start: the process finished, nothing was interrupted.
    assert bj.interrupted_since(state, since + timedelta(minutes=20)) is None
    assert not bj.backup_due(since + timedelta(minutes=30), 3, AMS, since + timedelta(minutes=20), state)
    # Nothing running, or a value that is no timestamp: no interrupted attempt.
    assert bj.interrupted_since({}) is None and bj.interrupted_since({"running_since": "gisteren"}) is None


async def _backup_row(status: str, auto: bool, kind: str = "export") -> BackupRecord:
    async with sessionmaker()() as db:
        b = BackupRecord(
            kind=kind,
            filename=f"shc-{'auto-' if auto else ''}{uuid.uuid4().hex[:8]}.shcbak",
            status=status,
            auto=auto,
            detail={"secrets_included": True},
        )
        db.add(b)
        await db.commit()
        return b


async def _open_backup_alert() -> Alert | None:
    async with sessionmaker()() as db:
        return (
            await db.execute(select(Alert).where(Alert.key == bj.ALERT_KEY, Alert.resolved_at.is_(None)))
        ).scalar_one_or_none()


async def test_worker_start_records_interrupted_backup_as_failure(admin):
    s = get_settings()
    since = datetime.now(UTC) - timedelta(minutes=20)
    async with settings_restored("backup_state", "last_auto_backup", "last_auto_dump", "maintenance"):
        async with sessionmaker()() as db:
            await set_setting(db, "last_auto_backup", (since - timedelta(days=1)).isoformat())
            await set_setting(db, "last_auto_dump", None)
            await set_setting(
                db,
                "backup_state",
                {
                    "running_since": since.isoformat(),
                    "running_kind": "full",
                    "failures": 1,
                    "last_attempt": (since - timedelta(hours=3)).isoformat(),
                    "last_error": "Schijf vol.",
                    "last_ok": "2026-11-01T03:00:00+00:00",
                },
            )
            await set_setting(db, "maintenance", True)
            await db.commit()
        running_auto = await _backup_row("running", auto=True)
        running_dump = await _backup_row("running", auto=True, kind=bk.DUMP_KIND)
        running_manual = await _backup_row("running", auto=False)
        ok_auto = await _backup_row("ok", auto=True)
        partial = s.backup_dir / ".shc-auto-20261109-031500-abcd.shcbak.tmp"
        partial.write_bytes(b"half")
        manual_partial = s.backup_dir / ".shc-20261109-031500-abcd.shcbak.tmp"
        manual_partial.write_bytes(b"half")
        try:
            # A restore runs (maintenance): nothing is touched at start, the backup loop does it later.
            w = make_worker()
            assert await w.recover_backup_at_start() is False
            async with sessionmaker()() as db:
                assert "running_since" in await get_setting(db, "backup_state")
                await set_setting(db, "maintenance", False)
                await db.commit()

            # The real start path: Worker.run() (loops stubbed out) records the interrupted attempt.
            w = make_worker()

            async def nothing(*_a, **_k):
                return None

            w.load_integrations = nothing
            w.listen = nothing
            w.loop_specs = lambda: []
            await w.run()

            async with sessionmaker()() as db:
                st = await get_setting(db, "backup_state")
                rows = {
                    b.id: b
                    for b in (
                        await db.execute(
                            select(BackupRecord).where(
                                BackupRecord.id.in_([running_auto.id, running_dump.id, running_manual.id, ok_auto.id])
                            )
                        )
                    ).scalars()
                }
            assert st["failures"] == 2 and st["last_error"] == "Back-up onderbroken (worker herstart)"
            assert st["last_attempt"] == since.isoformat() and "running_since" not in st
            assert st["last_ok"] == "2026-11-01T03:00:00+00:00"
            # existing back-off: second failure -> 2 h after the interrupted start
            assert bj.retry_after(st) == since + timedelta(hours=2)
            assert not bj.backup_due(datetime.now(UTC), 3, AMS, since - timedelta(days=1), st)
            for rid in (running_auto.id, running_dump.id):
                assert rows[rid].status == "failed"
                assert rows[rid].detail["error"] == "Back-up onderbroken (worker herstart)"
                assert rows[rid].detail["secrets_included"] is True  # rest of the detail kept
            assert rows[running_manual.id].status == "running"  # manual backups are the API's business
            assert rows[ok_auto.id].status == "ok"
            assert not partial.exists() and manual_partial.exists()
            a = await _open_backup_alert()
            assert a is not None and a.title == "Automatische back-up mislukt"
            assert a.detail.startswith("Back-up onderbroken (worker herstart). Volgende poging rond ")
            r = (await admin.get("/system/backup-settings")).json()
            assert r["failures"] == 2 and r["last_error"] == "Back-up onderbroken (worker herstart)"
            assert r["due_now"] is False

            # Recorded once: a second start (or backup round) finds nothing to do.
            assert await make_worker().recover_backup_at_start() is False
            async with sessionmaker()() as db:
                assert (await get_setting(db, "backup_state"))["failures"] == 2
        finally:
            manual_partial.unlink(missing_ok=True)
            async with sessionmaker()() as db:
                for b in (running_auto, running_dump, running_manual, ok_auto):
                    row = await db.get(BackupRecord, b.id)
                    if row is not None:
                        await db.delete(row)
                await db.commit()


async def test_backup_round_records_interrupted_attempt_even_when_backups_are_off(admin):
    since = datetime.now(UTC) - timedelta(minutes=5)
    async with settings_restored("backup_state", "last_auto_backup", "last_auto_dump"):
        assert (await admin.put("/system/backup-settings", {"clear_passphrase": True})).status_code == 200
        async with sessionmaker()() as db:
            await set_setting(db, "last_auto_backup", None)
            await set_setting(db, "last_auto_dump", None)
            await set_setting(db, "backup_state", {"running_since": since.isoformat(), "running_kind": "dump"})
            await db.commit()
        w = make_worker()
        await w.backup_round()
        async with sessionmaker()() as db:
            st = await get_setting(db, "backup_state")
        assert st["failures"] == 1 and st["last_error"] == bj.INTERRUPTED_ERROR and "running_since" not in st
        assert (await _open_backup_alert()) is not None


async def test_backup_that_finished_before_the_worker_stopped_is_not_a_failure(admin):
    since = datetime.now(UTC) - timedelta(minutes=30)
    async with settings_restored("backup_state", "last_auto_backup", "last_auto_dump"):
        async with sessionmaker()() as db:
            # The backup process wrote its result (last_auto_dump), the worker died before recording it.
            await set_setting(db, "last_auto_backup", None)
            await set_setting(db, "last_auto_dump", (since + timedelta(minutes=3)).isoformat())
            await set_setting(
                db,
                "backup_state",
                {"running_since": since.isoformat(), "running_kind": "dump", "failures": 2, "last_error": "x"},
            )
            await raise_alert(db, None, bj.ALERT_KEY, "error", "Automatische back-up mislukt", "x", "backup")
            await db.commit()
        assert await make_worker().recover_backup_at_start() is True
        async with sessionmaker()() as db:
            st = await get_setting(db, "backup_state")
        assert st["failures"] == 0 and "running_since" not in st and "last_error" not in st
        assert await _open_backup_alert() is None


async def test_restore_drops_the_running_marker_of_the_backup_itself(tmp_path):
    """A full export made by the automatic backup contains its own "running_since": after a restore that
    is no interrupted attempt on this server."""
    async with temp_db() as url:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as c:
                await c.execute(
                    text("INSERT INTO settings (key, value) VALUES ('backup_state', CAST(:v AS jsonb))"),
                    {
                        "v": '{"v": {"running_since": "2026-11-09T03:00:00+00:00", "running_kind": "full", "failures": 1}}'
                    },
                )
            rec = await bk.run_backup(PW, True, url=url, backup_dir=tmp_path)
            await bk.restore_file(tmp_path / rec["filename"], PW, url=url)
            async with engine.connect() as c:
                st = (await c.execute(text("SELECT value -> 'v' FROM settings WHERE key = 'backup_state'"))).scalar()
        finally:
            await engine.dispose()
    if isinstance(st, str):
        import json

        st = json.loads(st)
    assert st == {"failures": 1}


# --- 2. retention of the other raw sensor readings ------------------------------------------------

SENSOR_METRICS = (
    "temperature",
    "humidity",
    "target_c",
    "unit_temperature",
    "battery",
    "link_quality",
    "pressure",
    "battery_voltage",
    "co2_ppm",  # any other non-energy metric
)
KEPT_METRICS = ("energy_import_kwh", "gas_m3", "water_m3")  # meter counters: never by sensor retention


async def test_sensor_retention_setting_validation_and_batched_cleanup(admin, monkeypatch):
    assert agg.RETENTION_MIN["sensor_days"] == max(30, ad.BASELINE_DAYS)
    s = get_settings()
    assert s.retention_sensor_days == 400
    # The environment default never goes below the minimum either.
    assert agg.retention_defaults(s.model_copy(update={"retention_sensor_days": 5}))["sensor_days"] == 30
    async with settings_restored("retention"):
        async with sessionmaker()() as db:
            await set_setting(db, "retention", {})
            await db.commit()
        v = (await admin.get("/system/retention")).json()
        assert v["sensor_days"] == 400 and v["source"]["sensor_days"] == "env"
        assert v["defaults"]["sensor_days"] == 400 and v["minimums"]["sensor_days"] == 30
        assert "Sensormetingen" in v["note"]
        for body, word in (({"sensor_days": 14}, "minimaal 30"), ({"sensor_days": 99999}, "maximaal")):
            r = await admin.put("/system/retention", body)
            assert r.status_code == 422 and word in r.json()["detail"]["message"], r.text
            assert "Sensormetingen" in r.json()["detail"]["message"]
        r = await admin.put("/system/retention", {"sensor_days": 60})
        assert r.status_code == 200, r.text
        v = r.json()
        assert v["sensor_days"] == 60 and v["source"]["sensor_days"] == "app"
        assert v["source"]["power_days"] == "env" and v["log_days"] == s.retention_log_days  # others untouched
        audit = (await admin.get("/audit?action=retention_updated&limit=1")).json()
        assert audit and audit[0]["detail"] == {"sensor_days": 60}

        home_id = await new_home(admin)
        sensor = await add_device(home_id, "zigbee_sensor", "Sensor retentie")
        meter = await add_device(home_id, "p1_meter", "Meter retentie")
        now = datetime.now(UTC)
        old, recent = now - timedelta(days=90), now - timedelta(days=10)
        async with sessionmaker()() as db:
            for m in SENSOR_METRICS:
                for i in range(5):
                    db.add(_reading(sensor.id, m, old - timedelta(minutes=i)))
                db.add(_reading(sensor.id, m, recent))
            for m in KEPT_METRICS:  # never aggregated: no hourly totals yet
                for i in range(3):
                    db.add(_reading(meter.id, m, old - timedelta(minutes=i)))
            await db.commit()
        # Small batches: the delete has to go round several times to remove 45 rows.
        monkeypatch.setattr(agg, "DELETE_BATCH", 4)
        monkeypatch.setattr(agg, "DELETE_MAX_BATCHES", 100_000)
        out = await make_worker().retention_round()
        assert out["sensors"] >= len(SENSOR_METRICS) * 5
        left = await _counts(sensor.id)
        assert left == {m: 1 for m in SENSOR_METRICS}  # only the 10-day-old reading of each metric
        assert await _counts(meter.id) == {m: 3 for m in KEPT_METRICS}

        # A stored value below the minimum is ignored (the default applies).
        async with sessionmaker()() as db:
            await set_setting(db, "retention", {"sensor_days": 5})
            await db.commit()
        v = (await admin.get("/system/retention")).json()
        assert v["sensor_days"] == 400 and v["source"]["sensor_days"] == "env"
        r = await admin.put("/system/retention", {"sensor_days": 120})
        r = await admin.put("/system/retention", {"sensor_days": None})
        assert r.json()["sensor_days"] == 400 and r.json()["source"]["sensor_days"] == "env"


def _reading(device_id, metric: str, at: datetime) -> SensorReading:
    return SensorReading(
        device_id=device_id,
        metric=metric,
        value=1.0,
        unit="",
        observed_at=at,
        event_id=f"retentie-{uuid.uuid4().hex}",
    )


async def _counts(device_id) -> dict[str, int]:
    async with sessionmaker()() as db:
        rows = (
            await db.execute(
                select(SensorReading.metric, func.count())
                .where(SensorReading.device_id == device_id)
                .group_by(SensorReading.metric)
            )
        ).all()
    return {m: n for m, n in rows}


# --- 3. HomeWizard gets the home's time zone -----------------------------------------------------


async def test_homewizard_integration_gets_the_home_time_zone(admin, monkeypatch):
    from shc_integrations import registry
    from shc_integrations.homewizard.adapter import HomeWizardAdapter, normalize_v2

    class QuietHomeWizard(HomeWizardAdapter):
        async def poll(self):
            return []

        async def subscribe(self):
            return
            yield

        async def close(self):
            return None

    class Other:
        def __init__(self, config, credentials, **_kw):
            self.config = dict(config)

        async def poll(self):
            return []

        async def subscribe(self):
            return
            yield

        async def close(self):
            return None

    monkeypatch.setitem(registry.ADAPTERS, "homewizard", QuietHomeWizard)
    monkeypatch.setitem(registry.ADAPTERS, "hue", Other)
    r = await admin.post("/homes", json={"name": f"New York {uuid.uuid4().hex[:6]}", "timezone": "America/New_York"})
    assert r.status_code == 200, r.text
    home_id = uuid.UUID(r.json()["id"])
    p1 = await add_integration(home_id, "homewizard", "P1", config={"host": "192.168.1.3"})
    own = await add_integration(
        home_id, "homewizard", "Stopcontact", config={"host": "192.168.1.4", "timezone": "Europe/London"}
    )
    hue = await add_integration(home_id, "hue", "Hue", config={"host": "192.168.1.5"})
    w = make_worker()
    try:
        await w.load_integrations({home_id})
        a = w.adapters[p1.id]
        assert a.config["timezone"] == "America/New_York" and a._tz == ZoneInfo("America/New_York")
        assert w.adapters[own.id].config["timezone"] == "Europe/London"  # set on the integration: kept
        assert "timezone" not in w.adapters[hue.id].config
        # Only the running adapter gets it: the stored config (and so the fingerprint) is unchanged.
        async with sessionmaker()() as db:
            row = await db.get(Integration, p1.id)
        assert "timezone" not in row.config and w.fingerprints[p1.id] == fingerprint(row)
        n = len(w.adapters)
        await w.load_integrations({home_id})
        assert w.adapters[p1.id] is a and len(w.adapters) == n  # not restarted on the next reload

        # A zone-less v2 timestamp is local time of the home: 12:00 in New York in July = 16:00 UTC.
        polled = datetime(2026, 7, 1, 16, 0, 5, tzinfo=UTC)
        out = normalize_v2("abc", {"timestamp": "2026-07-01T12:00:00", "power_w": 5}, polled, a._tz)
        assert out[0].observed_at == datetime(2026, 7, 1, 16, 0, tzinfo=UTC)
    finally:
        for iid in list(w.adapters):
            await w.stop_integration(iid, "test klaar")


# --- 4. alerts: key in the view, an open alert follows the new title ------------------------------


async def test_alert_view_has_key_and_reraise_updates_title(admin, home, monkeypatch):
    from shc_api import events

    published: list[tuple[str, dict]] = []
    real = events.publish

    async def spy(db, type_, home_id, data):
        published.append((type_, data))
        await real(db, type_, home_id, data)

    monkeypatch.setattr(events, "publish", spy)
    home_id = uuid.UUID(home["id"])
    key = f"watchdog:{uuid.uuid4()}"
    async with sessionmaker()() as db:
        a = await raise_alert(db, home_id, key, "warn", "Woonkamer warmt niet op", "Na 30 minuten.", "climate")
        await db.commit()
    assert a is not None
    created = [d for t, d in published if t == "alert.created"]
    assert created and created[-1]["key"] == key
    view = alert_view(a)
    assert set(view) == {"id", "key", "severity", "title", "detail", "created_at", "acknowledged", "source"}
    assert view["key"] == key and view["title"] == "Woonkamer warmt niet op"

    # Same cause, zone renamed: the open alert keeps its id and gets the new title and detail.
    async with sessionmaker()() as db:
        again = await raise_alert(db, home_id, key, "warn", "Zitkamer warmt niet op", "Na 45 minuten.", "climate")
        await db.commit()
        row = await db.get(Alert, a.id)
        n_open = (
            await db.execute(
                select(func.count()).select_from(Alert).where(Alert.key == key, Alert.resolved_at.is_(None))
            )
        ).scalar_one()
    assert again is None and n_open == 1
    assert row.title == "Zitkamer warmt niet op" and row.detail == "Na 45 minuten."

    listed = (await admin.get(f"/homes/{home_id}/alerts")).json()
    mine = [x for x in listed if x["id"] == str(a.id)]
    assert mine and mine[0]["key"] == key and mine[0]["title"] == "Zitkamer warmt niet op"
    overview = (await admin.get(f"/homes/{home_id}/overview")).json()
    assert any(x["key"] == key for x in overview["alerts"])
    acked = (await admin.post(f"/alerts/{a.id}/ack")).json()
    assert acked["key"] == key and acked["acknowledged"] is True

    async with sessionmaker()() as db:
        await resolve_alert(db, key)
        await db.commit()
