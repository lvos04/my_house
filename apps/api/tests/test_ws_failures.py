"""Failure scenarios with fakes (verbeterlijst 42): the database connection drops during a worker loop,
MQTT drops and comes back under the real Zigbee adapter, the Zigbee2MQTT bridge (USB dongle) goes
offline, a real HomeWizard adapter loses its device and gets it back, and a button's climate action
goes through request_zone_command into the command queue and onto a (fake) unit."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import asyncpg
import pytest
from sqlalchemy import select, text

from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import Alert, ClimateZone, CommandRecord, Device, Integration, ZoneLog
from shc_api.store import get_setting, set_setting

from .ws_devices_support import INTEGRATION_TESTS, add_device, add_integration, attach, fetch, make_worker, new_home


@pytest.fixture(autouse=True)
async def _default_home_first(home):
    """The shared default home must exist before these tests add their own homes."""
    return home


@pytest.fixture
async def commands_allowed():
    async with sessionmaker()() as db:
        before = bool(await get_setting(db, "safe_mode", False))
        await set_setting(db, "safe_mode", False)
        await db.commit()
    yield
    async with sessionmaker()() as db:
        await set_setting(db, "safe_mode", before)
        await db.commit()


async def wait_for(pred, limit_s: float = 10.0):
    """Await until ``pred()`` (sync or async) is truthy."""
    deadline = asyncio.get_running_loop().time() + limit_s
    while True:
        result = pred()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return result
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0.05)


async def open_alerts(prefix: str) -> list[Alert]:
    async with sessionmaker()() as db:
        q = select(Alert).where(Alert.key.like(f"{prefix}%"), Alert.resolved_at.is_(None))
        return list((await db.execute(q)).scalars())


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# --- PostgreSQL connection drops during a worker loop ---------------------------------------------


async def test_worker_loop_survives_a_dropped_database_connection(caplog):
    """The backend serving a loop iteration is terminated mid-query (like a database restart): the
    iteration fails with one warning line (no traceback), the loop backs off and carries on with a
    fresh pooled connection."""
    w = make_worker()
    pids: list[int] = []
    done: list[int] = []

    async def work() -> None:
        async with sessionmaker()() as db:
            if not pids:
                pids.append((await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                await db.execute(text("SELECT pg_sleep(30)"))  # terminated underneath
            await db.execute(text("SELECT 1"))
        done.append(1)

    caplog.set_level(logging.INFO, logger="shc.worker")
    task = asyncio.create_task(w._forever("dbdrop", work, 0.01))
    try:
        await wait_for(lambda: pids)
        await asyncio.sleep(0.2)
        conn = await asyncpg.connect(get_settings().database_url.replace("+asyncpg", ""))
        try:
            assert await conn.fetchval("SELECT pg_terminate_backend($1)", pids[0]) is True
        finally:
            await conn.close()
        await wait_for(lambda: len(done) >= 3)  # the loop continues
    finally:
        await stop(task)
    failures = [r for r in caplog.records if "Lus dbdrop faalde" in r.getMessage()]
    assert len(failures) == 1 and failures[0].levelno == logging.WARNING and failures[0].exc_info is None
    assert w.stuck_loops() == []


def test_dropped_database_errors_count_as_expected():
    from sqlalchemy.exc import DBAPIError, OperationalError

    from shc_api.worker.core import is_db_unavailable, is_expected_error

    dropped = DBAPIError("SELECT 1", {}, Exception("x"), connection_invalidated=True)
    assert is_db_unavailable(dropped) and is_expected_error(dropped)
    assert is_expected_error(OperationalError("SELECT 1", {}, Exception("server closed the connection")))
    assert is_expected_error(asyncpg.exceptions.ConnectionDoesNotExistError("weg"))
    assert not is_expected_error(DBAPIError("SELECT 1", {}, Exception("syntax error")))  # a real bug: traceback
    assert not is_expected_error(ValueError("bug"))


# --- Zigbee: MQTT drops and reconnects, the bridge (USB dongle) goes offline ---------------------


class FakeBroker:
    """Scripted MQTT broker for the real ZigbeeAdapter: each connect takes the next scripted session
    ("refuse" or a FakeSession), and waits until the test provides one."""

    DROP = object()

    def __init__(self) -> None:
        self.next: asyncio.Queue = asyncio.Queue()
        self.connects = 0

    def session(self) -> FakeSession:
        s = FakeSession()
        self.next.put_nowait(s)
        return s

    def refuse(self) -> None:
        self.next.put_nowait("refuse")

    def client_class(self):
        import aiomqtt

        broker = self

        class Client:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.session: FakeSession | None = None

            async def __aenter__(self):
                item = await broker.next.get()
                broker.connects += 1
                if item == "refuse":
                    raise aiomqtt.MqttError("[Errno 111] Connection refused")
                self.session = item
                return self

            async def __aexit__(self, *exc) -> bool:
                return False

            async def subscribe(self, topic, qos=0) -> None:
                assert self.session is not None
                self.session.subscribed.append(topic)

            @property
            def messages(self):
                session = self.session

                async def gen():
                    while True:
                        item = await session.queue.get()
                        if item is FakeBroker.DROP:
                            raise aiomqtt.MqttError("Disconnected during message iteration")
                        yield SimpleNamespace(topic=item[0], payload=item[1])

                return gen()

        return Client


class FakeSession:
    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.subscribed: list[str] = []

    def send(self, topic: str, payload: dict | str) -> None:
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        self.queue.put_nowait((f"zigbee2mqtt/{topic}", raw.encode()))

    def drop(self) -> None:
        self.queue.put_nowait(FakeBroker.DROP)


async def _coordinator(home_id) -> Device | None:
    async with sessionmaker()() as db:
        q = select(Device).where(Device.home_id == home_id, Device.stable_identity == "zigbee2mqtt-bridge")
        return (await db.execute(q)).scalar_one_or_none()


async def _health_rounds(w, n: int) -> None:
    for _ in range(n):
        await w.integration_health_round()


@pytest.fixture
async def zigbee(admin, monkeypatch):
    """A home with a Zigbee integration, run by the worker's real subscribe/pump path on the real
    ZigbeeAdapter, connected to a scripted broker."""
    import aiomqtt
    from shc_integrations.zigbee import ZigbeeAdapter

    broker = FakeBroker()
    monkeypatch.setattr(aiomqtt, "Client", broker.client_class())

    async def no_wait(_s: float) -> None:  # reconnect back-off: paced by the broker script instead
        await asyncio.sleep(0)

    home_id = await new_home(admin)
    integ = await add_integration(home_id, "zigbee", "Zigbee", config={"host": "mosquitto"})
    adapter = ZigbeeAdapter({"host": "mosquitto"}, sleep=no_wait)
    w = make_worker()
    attach(w, integ, adapter)
    task = asyncio.create_task(w.subscribe(integ.id))
    yield SimpleNamespace(home_id=home_id, integ=integ, adapter=adapter, worker=w, broker=broker)
    await stop(task)


async def test_zigbee_mqtt_disconnect_and_reconnect(admin, zigbee):
    from shc_api.views.status import status_checks, system_checks

    z = zigbee
    s1 = z.broker.session()
    s1.send("bridge/state", {"state": "online"})
    coord = await wait_for(_coordinator_online(z.home_id, True))
    assert coord.kind == "zigbee_bridge" and coord.state["mqtt_connected"] is True
    assert s1.subscribed == ["zigbee2mqtt/#"]
    await z.worker.integration_health_round()
    assert (await fetch(Integration, z.integ.id)).state == "ok"

    # The broker goes away: one status event from the adapter, the coordinator turns offline.
    s1.drop()
    coord = await wait_for(_coordinator_online(z.home_id, False))
    assert coord.state["mqtt_connected"] is False and "Disconnected" in coord.state["mqtt_error"]
    z.broker.refuse()  # the first reconnect attempt fails as well
    await wait_for(lambda: z.broker.connects >= 2)
    # Health flips to error only after the debounce (3 checks), then raises the MQTT alert.
    await _health_rounds(z.worker, 2)
    assert (await fetch(Integration, z.integ.id)).state == "ok"
    await _health_rounds(z.worker, 1)
    row = await fetch(Integration, z.integ.id)
    assert row.state == "error" and row.diagnostics["status"]["code"] == "mqtt_disconnected"
    assert [a.title for a in await open_alerts(f"integration:{z.integ.id}")] == ["Zigbee: geen verbinding met MQTT"]
    async with sessionmaker()() as db:
        system = {c["key"]: c for c in await system_checks(db)}
        overview = {c["key"]: c for c in await status_checks(db, z.home_id)}
    assert system[f"integration:{z.integ.id}:mqtt"]["state"] == "error"
    assert overview[f"integration:{z.integ.id}"]["state"] == "error"

    # The broker is back: the adapter reconnects by itself, health recovers, the alert is resolved.
    s2 = z.broker.session()
    s2.send("bridge/state", {"state": "online"})
    await wait_for(_coordinator_online(z.home_id, True))
    await z.worker.integration_health_round()
    row = await fetch(Integration, z.integ.id)
    assert row.state == "ok" and row.diagnostics["health"]["extra"]["mqtt_connected"] is True
    assert await open_alerts(f"integration:{z.integ.id}") == []
    assert z.broker.connects == 3


async def test_zigbee_bridge_offline_raises_the_dongle_alert(admin, zigbee):
    from shc_api.views.status import system_checks

    z = zigbee
    s = z.broker.session()
    s.send("bridge/state", {"state": "online"})
    await wait_for(_coordinator_online(z.home_id, True))
    await z.worker.integration_health_round()
    # Zigbee2MQTT reports the coordinator (USB dongle) offline; MQTT itself stays connected.
    s.send("bridge/state", "offline")
    coord = await wait_for(_coordinator_online(z.home_id, False))
    assert coord.state["bridge_state"] == "offline" and coord.state["mqtt_connected"] is True
    await _health_rounds(z.worker, 3)
    row = await fetch(Integration, z.integ.id)
    assert row.state == "error" and row.diagnostics["status"]["code"] == "bridge_offline"
    alerts = await open_alerts(f"integration:{z.integ.id}")
    assert [a.title for a in alerts] == ["Zigbee-dongle offline"] and "USB-dongle" in alerts[0].detail
    async with sessionmaker()() as db:
        checks = {c["key"]: c for c in await system_checks(db)}
    assert checks[f"integration:{z.integ.id}:mqtt"]["state"] == "ok"
    assert checks[f"integration:{z.integ.id}:dongle"]["state"] == "error"
    # Plugged back in.
    s.send("bridge/state", {"state": "online"})
    await wait_for(_coordinator_online(z.home_id, True))
    await z.worker.integration_health_round()
    assert (await fetch(Integration, z.integ.id)).state == "ok"
    assert await open_alerts(f"integration:{z.integ.id}") == []


def _coordinator_online(home_id, online: bool):
    async def check():
        d = await _coordinator(home_id)
        return d if d is not None and d.online is online else None

    return check


# --- a real HomeWizard adapter loses its device and gets it back ---------------------------------


@pytest.fixture
def fake_http():
    if str(INTEGRATION_TESTS) not in sys.path:
        sys.path.append(str(INTEGRATION_TESTS))
    import fake_http

    return fake_http


def _fixture(name: str) -> dict:
    return json.loads((INTEGRATION_TESTS / "fixtures" / "homewizard" / name).read_text())


async def test_device_offline_then_back_debounced(admin, fake_http, caplog):
    from shc_integrations.homewizard import HomeWizardAdapter

    info = _fixture("v1_api_p1.json")
    routes = {
        ("GET", "/api"): lambda b, h: (200, info),
        ("GET", "/api/v1/data"): lambda b, h: (200, _fixture("v1_data_p1.json")),
    }
    home_id = await new_home(admin)
    caplog.set_level(logging.INFO, logger="shc.worker")
    async with fake_http.FakeHttpDevice(routes) as dev:
        cfg = {
            "host": dev.host,
            "serial": info["serial"],
            "product_type": "HWE-P1",
            "api_version": "v1",
            "min_poll_interval_s": 0,
        }
        integ = await add_integration(home_id, "homewizard", "P1-meter", config=cfg, identity=info["serial"])
        w = make_worker()
        attach(w, integ, HomeWizardAdapter(cfg))

        await w.poll(integ.id)
        assert (await fetch(Integration, integ.id)).state == "ok"
        async with sessionmaker()() as db:
            meter = (
                await db.execute(select(Device).where(Device.home_id == home_id, Device.integration_id == integ.id))
            ).scalar_one()
        assert meter.online is True

        dev.server.close()  # the meter drops off the network (connection refused)
        await dev.server.wait_closed()
        await w.poll(integ.id)
        await w.poll(integ.id)
        row = await fetch(Integration, integ.id)
        assert row.state == "ok" and (await fetch(Device, meter.id)).online is True  # debounced
        assert await open_alerts(f"integration:{integ.id}") == []
        await w.poll(integ.id)
        row = await fetch(Integration, integ.id)
        assert row.state == "error" and row.diagnostics["status"]["code"] == "device_offline"
        assert (
            row.detail
            == f"HomeWizard {dev.host} niet bereikbaar: geen verbinding (staat het apparaat aan en klopt het adres?)"
        )
        assert row.diagnostics["status"]["missed_polls"] == 3
        assert (await fetch(Device, meter.id)).online is False
        assert [a.title for a in await open_alerts(f"integration:{integ.id}")] == ["P1-meter niet bereikbaar"]

        # Back on the same address.
        dev.server = await asyncio.start_server(dev._handle, "127.0.0.1", dev.port)
        await w.poll(integ.id)
        row = await fetch(Integration, integ.id)
        assert row.state == "ok" and row.detail == "Verbonden"
        assert await open_alerts(f"integration:{integ.id}") == []
        assert (await fetch(Device, meter.id)).online is True
        await w.adapters[integ.id].close()
    transitions = [r.getMessage() for r in caplog.records if "Koppeling P1-meter:" in r.getMessage()]
    assert len(transitions) == 3  # unknown -> ok, ok -> error, error -> ok: one line per change


# --- a button's climate action goes through request_zone_command ----------------------------------


CAPS = {"modes": ["off", "heat", "cool", "dry", "fan", "auto"], "min_c": 16, "max_c": 30, "step_c": 1.0, "fan": True}


class FakeUnit:
    """A Gree-like unit: applies set_hvac and reports the state back (confirmed readback)."""

    def __init__(self, mode: str, target: float) -> None:
        self.state = {"power": mode != "off", "mode": mode, "target_c": target, "fan": "auto"}
        self.calls: list = []

    async def execute(self, command):
        from shc_integrations.base import CommandResult

        self.calls.append(command)
        p = command.params
        if "power" in p:
            self.state["power"] = bool(p["power"])
        for k in ("mode", "target_c", "fan"):
            if k in p:
                self.state[k] = p[k]
        return CommandResult(ok=True, confirmed=True, state=dict(self.state, caps=CAPS))


async def test_button_climate_action_goes_through_request_zone_command(admin, monkeypatch, commands_allowed):
    from shc_integrations.base import ButtonEvent, DeviceInfo

    from shc_api import commanding
    from shc_api.worker import ingest

    calls: list[dict] = []
    real = commanding.request_zone_command

    async def spy(db, zone, home, **kw):
        calls.append({"zone": zone.name, **kw})
        return await real(db, zone, home, **kw)

    monkeypatch.setattr(ingest, "request_zone_command", spy)
    home_id = await new_home(admin)
    hue = await add_integration(home_id, "hue", "Hue Bridge", config={"host": "192.168.1.2"})
    gree = await add_integration(home_id, "gree", "Gree", config={"host": "192.168.1.70"})
    living_unit = await add_device(
        home_id, "ac_unit", "Airco woonkamer", provider="gree", integration_id=gree.id,
        state={"caps": CAPS, "power": True, "mode": "heat", "target_c": 20.0},
    )  # fmt: skip
    bed_unit = await add_device(
        home_id, "ac_unit", "Airco slaapkamer", provider="gree", integration_id=gree.id,
        state={"caps": CAPS, "power": True, "mode": "heat", "target_c": 19.0},
    )  # fmt: skip
    zones = {z["name"]: z["id"] for z in (await admin.get(f"/homes/{home_id}/zones")).json()}
    for name, unit in (("Woonkamer", living_unit), ("Slaapkamer", bed_unit)):
        r = await admin.c.patch(
            f"/api/v1/zones/{zones[name]}", json={"unit_device_id": str(unit.id)}, headers=admin.h()
        )
        assert r.status_code == 200, r.text
    w = make_worker()
    attach(w, hue, SimpleNamespace(device_info=lambda _r: None))
    unit = FakeUnit("heat", 20.0)
    attach(w, gree, SimpleNamespace())
    w._adapter_for = lambda d: unit if d.id == living_unit.id else None  # type: ignore[method-assign]
    rid = "6" * 8 + "-0000-4000-8000-000000000001"
    b_warm, b_cool = "6" * 8 + "-0000-4000-8000-0000000000b1", "6" * 8 + "-0000-4000-8000-0000000000b2"
    now = datetime.now(UTC)
    buttons = [
        {"id": b_warm, "control_id": 1, "type": "button", "events": ["short_release"]},
        {"id": b_cool, "control_id": 2, "type": "button", "events": ["short_release"]},
    ]
    await w.handle_events(hue, [DeviceInfo(rid, "button", "Knop bank", now, extra={"buttons": buttons})])
    async with sessionmaker()() as db:
        btn = (await db.execute(select(Device).where(Device.stable_identity == rid))).scalar_one()
    r = await admin.put(
        f"/buttons/{btn.id}/mappings",
        json=[
            {
                "button": b_warm,
                "event": "short_release",
                "mode": "climate",
                "action": {"steps": [{"zone_id": zones["Woonkamer"], "mode": "heat", "target_c": 23, "override_minutes": 30}]},
            },
            {
                "button": b_cool,
                "event": "short_release",
                "mode": "climate",
                "action": {"steps": [{"zone_id": zones["Woonkamer"], "mode": "cool", "target_c": 21}]},
            },
        ],
    )  # fmt: skip
    assert r.status_code == 200, r.text

    # 1. Warm: through request_zone_command into the queue, executed and confirmed on the unit.
    await w.handle_events(hue, [ButtonEvent(rid, b_warm, "short_release", now, event_id=f"e1:{b_warm}")])
    assert calls == [
        {
            "zone": "Woonkamer",
            "mode": "heat",
            "target_c": 23,
            "fan": None,
            "override_minutes": 30,
            "source": "button",
            "actor": "knop Knop bank",
            "log_name": "Knop Knop bank",
        }
    ]
    assert w.wake_commands.is_set()
    async with sessionmaker()() as db:
        z = await db.get(ClimateZone, uuid.UUID(zones["Woonkamer"]))
        assert z.control == "manual" and z.override_source == "button" and z.pending_command_id is not None
        cmd_id = z.pending_command_id
    await w.process_commands({home_id})
    assert unit.calls and unit.calls[0].params == {"power": True, "mode": "heat", "target_c": 23.0}
    async with sessionmaker()() as db:
        cmd = await db.get(CommandRecord, cmd_id)
        assert cmd.status == "confirmed" and cmd.source == "button" and cmd.actor == "knop Knop bank"

    # 2. Cool while the other zone on the same outdoor unit heats: refused by the same outdoor-unit
    #    check as the app, logged on the zone, nothing queued, nothing sent.
    n_before = len(unit.calls)
    await w.handle_events(hue, [ButtonEvent(rid, b_cool, "short_release", now, event_id=f"e2:{b_cool}")])
    assert len(calls) == 2 and calls[1]["mode"] == "cool"
    async with sessionmaker()() as db:
        q = select(CommandRecord).where(CommandRecord.device_id == living_unit.id)
        assert len((await db.execute(q)).scalars().all()) == 1
        logs = (
            (await db.execute(select(ZoneLog.message).where(ZoneLog.zone_id == uuid.UUID(zones["Woonkamer"]))))
            .scalars()
            .all()
        )
    assert any(m.startswith("Knop Knop bank: niet uitgevoerd") and "buitenunit" in m for m in logs), logs
    await w.process_commands({home_id})
    assert len(unit.calls) == n_before
