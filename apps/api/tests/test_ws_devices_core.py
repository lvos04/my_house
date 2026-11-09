"""Worker core: per-integration loading, stop/reload, debounced status, liveness, maintenance, health
round, clock check (verbeterlijst 22, 24, 26, 28, 36; contracts C2, C3, C4, C8)."""

import asyncio
import contextlib
import logging
import struct
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from shc_api.db import sessionmaker
from shc_api.models import Alert, Device, Home, Integration
from shc_api.store import get_setting, set_setting

from .ws_devices_support import add_device, add_integration, attach, fetch, make_worker, new_home


class FakeAdapter:
    instances: list["FakeAdapter"] = []

    def __init__(self, config, credentials, **kwargs):
        self.config = dict(config)
        self.credentials = dict(credentials)
        self.closed = False
        self.polls = 0
        self.poll_error: Exception | None = None
        self.health_result = None
        self.stream: list = []
        self.stream_error: Exception | None = None
        FakeAdapter.instances.append(self)

    async def poll(self):
        self.polls += 1
        if self.poll_error is not None:
            raise self.poll_error
        return []

    async def subscribe(self):
        for ev in self.stream:
            yield ev
        if self.stream_error is not None:
            raise self.stream_error

    async def health(self):
        from shc_integrations.base import Health, HealthState

        return self.health_result or Health(HealthState.OK, "Verbonden")

    def device_info(self, _identity):
        return None

    async def close(self):
        self.closed = True


def _fake_registry(monkeypatch):
    from shc_integrations import registry

    for name in list(registry.ADAPTERS):
        monkeypatch.setitem(registry.ADAPTERS, name, FakeAdapter)


async def _open_alerts(prefix: str) -> list[Alert]:
    async with sessionmaker()() as db:
        return list(
            (await db.execute(select(Alert).where(Alert.key.like(f"{prefix}%"), Alert.resolved_at.is_(None)))).scalars()
        )


async def _stop_all(w):
    for iid in list(w.adapters):
        await w.stop_integration(iid, "test klaar")


# --- items 22 and 24: loading one by one, stop/reload --------------------------------------------


async def test_key_mismatch_marks_only_that_integration_and_reload_follows_db(admin, monkeypatch):
    _fake_registry(monkeypatch)
    home_id = await new_home(admin)
    bad = await add_integration(home_id, "gree", "Airco slaapkamer", raw_credentials=b"\x01" + b"x" * 40)
    ok = await add_integration(home_id, "hue", "Hue Bridge", config={"host": "192.168.1.2"}, creds={"k": "v"})
    other = await add_integration(home_id, "homewizard", "P1", config={"host": "192.168.1.3"})
    w = make_worker()
    try:
        await w.load_integrations({home_id})
        assert ok.id in w.adapters and other.id in w.adapters and bad.id not in w.adapters
        assert w.adapters[ok.id].credentials == {"k": "v"}
        row = await fetch(Integration, bad.id)
        assert row.state == "error" and row.detail.startswith("Sleutel past niet, koppel opnieuw")
        alerts = await _open_alerts(f"integration:{bad.id}")
        assert len(alerts) == 1 and alerts[0].title == "Airco slaapkamer: sleutel past niet, koppel opnieuw"
        # The next reload does not retry (or log) the same broken key.
        n = len(FakeAdapter.instances)
        await w.load_integrations({home_id})
        assert len(FakeAdapter.instances) == n

        # Disabled -> stopped; changed config -> restarted with the new config; key fixed -> loaded.
        first_hw = w.adapters[other.id]
        async with sessionmaker()() as db:
            await db.execute(update(Integration).where(Integration.id == ok.id).values(enabled=False))
            await db.execute(
                update(Integration).where(Integration.id == other.id).values(config={"host": "192.168.1.33"})
            )
            from shc_api.config import get_settings
            from shc_api.security import SecretBox

            fixed = SecretBox(get_settings().resolved_secret_key()).encrypt({"key": "abc"})
            await db.execute(update(Integration).where(Integration.id == bad.id).values(credentials_enc=fixed))
            await db.commit()
        hue_adapter = w.adapters[ok.id]
        await w.load_integrations({home_id})
        assert ok.id not in w.adapters and hue_adapter.closed
        assert first_hw.closed and w.adapters[other.id] is not first_hw
        assert w.adapters[other.id].config["host"] == "192.168.1.33"
        assert bad.id in w.adapters and w.adapters[bad.id].credentials == {"key": "abc"}
        assert not await _open_alerts(f"integration:{bad.id}")

        # Archiving the home stops everything of that home.
        async with sessionmaker()() as db:
            await db.execute(update(Home).where(Home.id == home_id).values(active_to=datetime.now(UTC)))
            await db.commit()
        await w.load_integrations({home_id})
        assert not [i for i in w.adapters if i in (bad.id, other.id)]
    finally:
        await _stop_all(w)


# --- item 28: debounced status, one log line per change, status.updated --------------------------


async def test_status_debounce_identity_mismatch_and_status_updated(admin, monkeypatch, caplog):
    from shc_integrations.base import IdentityMismatch

    from shc_api import events

    published: list[str] = []
    real_publish = events.publish

    async def spy(db, type_, home_id, data):
        published.append(type_)
        await real_publish(db, type_, home_id, data)

    monkeypatch.setattr(events, "publish", spy)
    home_id = await new_home(admin)
    integ = await add_integration(home_id, "homewizard", "P1-meter", config={"host": "192.168.1.3"})
    meter = await add_device(home_id, "p1_meter", "P1", provider="homewizard", integration_id=integ.id)
    w = make_worker()
    fake = FakeAdapter({}, {})
    attach(w, integ, fake)
    caplog.set_level(logging.INFO, logger="shc.worker")

    await w.poll(integ.id)
    assert (await fetch(Integration, integ.id)).state == "ok"
    fake.poll_error = OSError("Connection refused")
    await w.poll(integ.id)
    await w.poll(integ.id)
    row = await fetch(Integration, integ.id)
    assert row.state == "ok", "two failed polls are not an error yet"
    assert (await fetch(Device, meter.id)).online is True
    published.clear()
    await w.poll(integ.id)
    row = await fetch(Integration, integ.id)
    assert row.state == "error" and row.detail == "Niet bereikbaar: geen verbinding met het apparaat"
    assert row.diagnostics["status"]["missed_polls"] == 3
    assert "status.updated" in published
    assert (await fetch(Device, meter.id)).online is False
    alerts = await _open_alerts(f"integration:{integ.id}")
    assert [a.title for a in alerts] == ["P1-meter niet bereikbaar"]
    await w.poll(integ.id)  # still failing: no new line, no new alert
    transitions = [r for r in caplog.records if "Koppeling P1-meter:" in r.getMessage()]
    assert len(transitions) == 2  # unknown -> ok, ok -> error
    assert not any(r.exc_info for r in caplog.records if r.name == "shc.worker"), "no tracebacks"

    fake.poll_error = None
    await w.poll(integ.id)
    assert (await fetch(Integration, integ.id)).state == "ok"
    assert not await _open_alerts(f"integration:{integ.id}")

    # Another device on the address: an error at once, with its own alert; devices stay online.
    async with sessionmaker()() as db:
        await db.execute(update(Device).where(Device.id == meter.id).values(online=True))
        await db.commit()
    fake.poll_error = IdentityMismatch("Op 192.168.1.3 antwoordt een ander HomeWizard-apparaat")
    await w.poll(integ.id)
    row = await fetch(Integration, integ.id)
    assert row.state == "error" and row.diagnostics["status"]["code"] == "identity_mismatch"
    alerts = await _open_alerts(f"integration:{integ.id}")
    assert [a.title for a in alerts] == ["P1-meter: apparaat op ander adres"]
    assert "DHCP" in alerts[0].detail
    assert (await fetch(Device, meter.id)).online is True
    # The overview shows the integration with its code.
    status = (await admin.get(f"/homes/{home_id}/overview")).json()["status"]
    entry = next(s for s in status if s["key"] == f"integration:{integ.id}")
    assert entry["state"] == "error" and entry["code"] == "identity_mismatch"


async def test_push_stream_batches_and_restarts(admin):
    from shc_integrations.base import AdapterError, DeviceInfo, StateUpdate

    home_id = await new_home(admin)
    integ = await add_integration(home_id, "zigbee", config={"host": "mqtt"})
    w = make_worker()
    w.push_retry_s = 0.01
    fake = FakeAdapter({}, {})
    now = datetime.now(UTC)
    fake.stream = [
        DeviceInfo("0x01", "zigbee_sensor", "Sensor A", now),
        StateUpdate("0x01", {"availability": "online"}, now),
        StateUpdate("0x02", {"availability": "online"}, now),
    ]
    attach(w, integ, fake)
    batches = []
    real = w.handle_events

    async def record(i, evs):
        batches.append(len(evs))
        await real(i, evs)

    w.handle_events = record
    await w._pump(integ.id, fake)
    assert batches == [3], "events that arrive together are stored in one transaction"
    # A stream that fails is restarted and reported (debounced).
    fake.stream, fake.stream_error = [], AdapterError("MQTT weg")
    task = asyncio.create_task(w.subscribe(integ.id))
    await asyncio.sleep(0.3)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert w.status[integ.id].sources["push"].state == "error"
    assert (await fetch(Integration, integ.id)).state == "error"


# --- contracts C2, C3, C8 -----------------------------------------------------------------------


async def test_loop_specs_contract_c2():
    w = make_worker()
    names = [s[0] for s in w.loop_specs()]
    assert {"heartbeat", "commands", "climate", "health", "reload", "backup", "aggregate", "retention", "live"} <= set(
        names
    )
    intervals = {s[0]: s[2] for s in w.loop_specs()}
    assert (intervals["aggregate"], intervals["retention"], intervals["live"]) == (300, 3600, 5)
    w.live_round = None  # energy loops missing: the worker still starts without them
    assert "live" not in [s[0] for s in w.loop_specs()]


async def test_heartbeat_liveness_file_and_stuck_loop(admin, tmp_path):
    w = make_worker(tmp_path)
    w.loop_interval = {"climate": 60.0, "heartbeat": 30.0}
    w.loop_seen = {"climate": time.monotonic(), "heartbeat": time.monotonic()}
    await w.heartbeat()
    alive = tmp_path / "shc-worker-alive"
    assert alive.exists() and datetime.fromisoformat(alive.read_text()) <= datetime.now(UTC)
    async with sessionmaker()() as db:
        loops = await get_setting(db, "worker_loops")
        assert set(loops["loops"]) == {"climate", "heartbeat"} and not loops["loops"]["climate"]["stuck"]
    alive.unlink()
    w.loop_seen["climate"] = time.monotonic() - (5 * 60 + 61)
    assert w.stuck_loops() == ["climate"]
    await w.heartbeat()
    assert not alive.exists(), "a stuck loop must make the container healthcheck fail"
    checks = (await admin.get("/system/status")).json()["checks"]
    loop_check = next(c for c in checks if c["key"] == "worker_loops")
    assert loop_check["state"] == "error" and "klimaatregeling" in loop_check["detail"]
    w.loop_seen["climate"] = time.monotonic()
    await w.heartbeat()
    assert alive.exists()


async def test_forever_pauses_while_maintenance(admin, tmp_path):
    w = make_worker(tmp_path)
    calls = []

    async def work():
        calls.append(1)

    async with sessionmaker()() as db:
        await set_setting(db, "maintenance", True)
        await db.commit()
    try:
        task = asyncio.create_task(w._forever("demo", work, 0.01))
        await asyncio.sleep(0.15)
        assert calls == [] and w.loop_seen["demo"] > 0
        # The heartbeat still touches the liveness file during maintenance, but writes nothing.
        async with sessionmaker()() as db:
            before = await get_setting(db, "worker_heartbeat")
        await w.heartbeat()
        assert (tmp_path / "shc-worker-alive").exists()
        async with sessionmaker()() as db:
            assert await get_setting(db, "worker_heartbeat") == before
        async with sessionmaker()() as db:
            await set_setting(db, "maintenance", False)
            await db.commit()
        w._maint_checked = -1e9  # skip the 10 s cache
        await asyncio.sleep(0.15)
        assert calls, "loops run again after maintenance"
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        async with sessionmaker()() as db:
            await set_setting(db, "maintenance", False)
            await db.commit()


# --- items 26 and 28: health round --------------------------------------------------------------


async def test_health_round_eventstream_alert_and_zigbee_parts(admin):
    from shc_integrations.base import Health, HealthState

    home_id = await new_home(admin)
    hue = await add_integration(home_id, "hue", "Hue Bridge", config={"host": "192.168.1.2"})
    zb = await add_integration(home_id, "zigbee", "Zigbee", config={"host": "mqtt"})
    w = make_worker()
    fh, fz = FakeAdapter({}, {}), FakeAdapter({}, {})
    attach(w, hue, fh)
    attach(w, zb, fz)
    since = datetime.now(UTC) - timedelta(minutes=7)
    fh.health_result = Health(
        HealthState.WARN,
        "Verbonden, maar knopgebeurtenissen komen niet binnen",
        code="eventstream_disconnected",
        extra={"eventstream_connected": False, "disconnected_since": since.isoformat(), "connected_since": None},
    )
    fz.health_result = Health(
        HealthState.ERROR,
        "Geen verbinding met de MQTT-broker",
        code="mqtt_disconnected",
        extra={"mqtt_connected": False, "mqtt_error": "Connection refused", "bridge_online": None},
    )
    await w.integration_health_round()
    alerts = await _open_alerts(f"eventstream:{hue.id}")
    assert [a.title for a in alerts] == ["Hue-knoppen werken niet"] and alerts[0].severity == "warn"
    assert (await fetch(Integration, hue.id)).state == "warn"
    assert (await fetch(Integration, zb.id)).state != "error", "one failed health check is not an error yet"
    await w.integration_health_round()
    await w.integration_health_round()
    assert (await fetch(Integration, zb.id)).state == "error"
    assert [a.title for a in await _open_alerts(f"integration:{zb.id}")] == ["Zigbee: geen verbinding met MQTT"]
    checks = {c["key"]: c for c in (await admin.get("/system/status")).json()["checks"]}
    assert checks[f"integration:{zb.id}:mqtt"]["state"] == "error"
    assert "Connection refused" in checks[f"integration:{zb.id}:mqtt"]["detail"]
    assert checks[f"integration:{zb.id}:dongle"]["state"] == "unknown"

    connected = datetime.now(UTC).isoformat()
    fh.health_result = Health(HealthState.OK, "Verbonden", extra={"connected_since": connected})
    fz.health_result = Health(
        HealthState.OK, "MQTT verbonden, Zigbee2MQTT online", extra={"mqtt_connected": True, "bridge_online": True}
    )
    await w.integration_health_round()
    assert not await _open_alerts(f"eventstream:{hue.id}")
    assert not await _open_alerts(f"integration:{zb.id}")
    detail = (await admin.get(f"/integrations/{hue.id}")).json()
    assert detail["connected_since"] == connected and detail["health"]["health_state"] == "ok"
    checks = {c["key"]: c for c in (await admin.get("/system/status")).json()["checks"]}
    assert checks[f"integration:{zb.id}:dongle"]["state"] == "ok"


# --- item 28: clock check against an NTP server --------------------------------------------------


class _FakeNtp(asyncio.DatagramProtocol):
    def __init__(self, skew: float):
        self.skew = skew

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        t = time.time() + self.skew
        secs = int(t) + 2_208_988_800
        frac = int((t % 1) * 2**32)
        ts = struct.pack("!II", secs, frac)
        reply = bytearray(48)
        reply[0] = 0x24  # version 4, mode 4 (server)
        reply[1] = 2  # stratum
        reply[24:32] = data[40:48]
        reply[32:40] = ts
        reply[40:48] = ts
        self.transport.sendto(bytes(reply), addr)


async def test_clock_check_against_sntp_server(admin):
    loop = asyncio.get_running_loop()
    w = make_worker()
    transport, _ = await loop.create_datagram_endpoint(lambda: _FakeNtp(100.0), local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("sockname")[1]
    try:
        w.settings = w.settings.model_copy(update={"ntp_server": f"127.0.0.1:{port}"})
        res = await w.clock_round(force=True)
        assert res["state"] == "error" and 99 < res["offset_s"] < 101
        assert await w.clock_round() is None, "checked at most every 15 minutes"
    finally:
        transport.close()
    transport, _ = await loop.create_datagram_endpoint(lambda: _FakeNtp(0.0), local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("sockname")[1]
    try:
        w.settings = w.settings.model_copy(update={"ntp_server": f"127.0.0.1:{port}"})
        res = await w.clock_round(force=True)
        assert res["state"] == "ok" and abs(res["offset_s"]) < 1
    finally:
        transport.close()
    checks = {c["key"]: c for c in (await admin.get("/system/status")).json()["checks"]}
    assert checks["time"]["state"] == "ok" and "Afwijking" in checks["time"]["detail"]
    w.settings = w.settings.model_copy(update={"ntp_server": None})
    assert (await w.clock_round(force=True))["state"] == "unknown"
    async with sessionmaker()() as db:
        await set_setting(db, "clock_check", {"state": "ok", "detail": "test"})
        await db.commit()


# --- item 22: overview shows worker heartbeat, safe mode, backup age; stale heartbeat alert -------


async def test_overview_worker_heartbeat_alert(admin):
    home_id = await new_home(admin)
    async with sessionmaker()() as db:
        before = await get_setting(db, "worker_heartbeat")
        await set_setting(db, "worker_heartbeat", (datetime.now(UTC) - timedelta(minutes=10)).isoformat())
        await db.commit()
    try:
        o = (await admin.get(f"/homes/{home_id}/overview")).json()
        keys = {s["key"]: s for s in o["status"]}
        assert keys["worker"]["state"] == "error" and "10 min" in keys["worker"]["detail"]
        assert "backup" in keys
        assert any(a["title"] == "Achtergronddienst reageert niet" for a in o["alerts"])
        # The worker resolves it with its next heartbeat.
        w = make_worker()
        w.alive_file = w.alive_file.with_name(f"alive-{uuid.uuid4().hex}")
        await w.heartbeat()
        w.alive_file.unlink(missing_ok=True)
        o = (await admin.get(f"/homes/{home_id}/overview")).json()
        assert {s["key"]: s for s in o["status"]}["worker"]["state"] == "ok"
        assert not any(a["title"] == "Achtergronddienst reageert niet" for a in o["alerts"])
    finally:
        async with sessionmaker()() as db:
            await set_setting(db, "worker_heartbeat", before or datetime.now(UTC).isoformat())
            await db.commit()
