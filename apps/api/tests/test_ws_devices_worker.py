"""Worker ingest with real adapter event shapes (verbeterlijst 8, 15, 25, 27, small item "Hue-scènes")."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import func, select

from shc_api.db import sessionmaker
from shc_api.models import (
    Alert,
    ClimateZone,
    CommandRecord,
    Device,
    DeviceBinding,
    LogicalRole,
    ProcessedEvent,
    SensorReading,
)

from .ws_devices_support import add_device, add_integration, attach, fetch, hue_inventory, make_worker, new_home

NEW = "0x00158d0009f1e2d3"
DEFINITION = {
    "model": "WSDCGQ11LM",
    "vendor": "Aqara",
    "description": "Temperature, humidity and pressure sensor",
    "exposes": [
        {"type": "numeric", "property": "battery"},
        {"type": "numeric", "property": "temperature"},
        {"type": "numeric", "property": "humidity"},
        {"type": "numeric", "property": "linkquality"},
    ],
}


async def _devices(home_id) -> dict[str, Device]:
    async with sessionmaker()() as db:
        rows = (await db.execute(select(Device).where(Device.home_id == home_id))).scalars()
        return {d.stable_identity: d for d in rows}


async def _latest(device_id, metric) -> SensorReading:
    async with sessionmaker()() as db:
        return (
            await db.execute(
                select(SensorReading)
                .where(SensorReading.device_id == device_id, SensorReading.metric == metric)
                .order_by(SensorReading.observed_at.desc())
                .limit(1)
            )
        ).scalar_one()


async def _open_alert(key) -> Alert | None:
    async with sessionmaker()() as db:
        return (
            await db.execute(select(Alert).where(Alert.key == key, Alert.resolved_at.is_(None)))
        ).scalar_one_or_none()


# --- item 8: Zigbee pairing sequence, kinds, names, coordinator, calibration ------------------------


async def test_zigbee_join_interview_availability_reading_replay(admin):
    from shc_integrations.zigbee import ZigbeeAdapter

    from shc_api.models import Integration

    home_id = await new_home(admin)
    r = await admin.post("/integrations/zigbee/setup", json={"home_id": str(home_id)})
    assert r.status_code == 200 and r.json()["kind"] == "zigbee_bridge"
    bridge_id = uuid.UUID(r.json()["id"])
    async with sessionmaker()() as db:
        integ = (
            await db.execute(
                select(Integration).where(Integration.home_id == home_id, Integration.provider == "zigbee")
            )
        ).scalar_one()
    a = ZigbeeAdapter({"host": "mqtt"})
    w = make_worker()
    attach(w, integ, a)

    async def feed(topic, payload):
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        await w.handle_events(integ, a.handle_message(f"zigbee2mqtt/{topic}", raw, datetime.now(UTC)))

    # Coordinator status lands on the existing zigbee_bridge device; no extra "bridge" device.
    await feed("bridge/state", {"state": "online"})
    await feed("bridge/info", {"version": "2.1.1", "coordinator": {"type": "EmberZNet", "ieee_address": "0x00124b"}})
    devs = await _devices(home_id)
    assert set(devs) == {"zigbee2mqtt-bridge"}
    bridge = devs["zigbee2mqtt-bridge"]
    assert bridge.id == bridge_id and bridge.kind == "zigbee_bridge" and bridge.online is True
    assert bridge.state["bridge_state"] == "online" and bridge.model == "EmberZNet"

    # 1. joined: device exists straight away, generic kind, readable name.
    await feed("bridge/event", {"type": "device_joined", "data": {"friendly_name": NEW, "ieee_address": NEW}})
    d = (await _devices(home_id))[NEW]
    assert d.kind == "zigbee_device" and d.name == "Zigbee-apparaat e2d3"
    # 2. interview started, 3. interview successful: upgraded to zigbee_sensor with vendor/model name.
    data = {"friendly_name": NEW, "ieee_address": NEW}
    await feed("bridge/event", {"type": "device_interview", "data": {**data, "status": "started"}})
    await feed(
        "bridge/event",
        {
            "type": "device_interview",
            "data": {**data, "status": "successful", "supported": True, "definition": DEFINITION},
        },
    )
    d = (await _devices(home_id))[NEW]
    assert d.kind == "zigbee_sensor" and d.model == "WSDCGQ11LM" and d.name == "Aqara WSDCGQ11LM (e2d3)"
    # Renamed in Zigbee2MQTT: the friendly_name becomes the name (the user did not rename it here).
    devices = [
        {"ieee_address": "0x00124b", "type": "Coordinator", "friendly_name": "Coordinator"},
        {
            "ieee_address": NEW,
            "type": "EndDevice",
            "friendly_name": "Slaapkamer sensor",
            "interview_completed": True,
            "supported": True,
            "definition": DEFINITION,
        },
    ]
    await feed("bridge/devices", devices)
    d = (await _devices(home_id))[NEW]
    assert d.name == "Slaapkamer sensor" and d.state["interview_completed"] is True
    # 4. availability
    await feed("Slaapkamer sensor/availability", {"state": "online"})
    assert (await _devices(home_id))[NEW].online is True
    # The app shows it as a sensor that a zone can use.
    listed = (await admin.get(f"/homes/{home_id}/devices")).json()
    assert [x["kind"] for x in listed if x["stable_identity"] == NEW] == ["zigbee_sensor"]

    # Calibration: offsets applied at ingest, raw value kept with the reading.
    r = await admin.c.patch(
        f"/api/v1/devices/{d.id}/calibration",
        json={"temperature_offset": -0.5, "humidity_offset": 2},
        headers=admin.h(),
    )
    assert r.status_code == 200 and r.json()["calibration"]["temperature_offset"] == -0.5
    # 5. measurement
    await feed("Slaapkamer sensor", {"temperature": 21.3, "humidity": 99.5, "battery": 90, "linkquality": 120})
    t = await _latest(d.id, "temperature")
    assert t.value == 20.8 and t.raw == {"raw_value": 21.3, "offset": -0.5}
    h = await _latest(d.id, "humidity")
    assert h.value == 100.0 and h.raw["raw_value"] == 99.5  # clamped to 100 %
    assert (await _latest(d.id, "battery")).raw is None  # not calibrated: no raw copy

    # A name given in the app is never overwritten by Zigbee2MQTT.
    r = await admin.c.patch(f"/api/v1/devices/{d.id}", json={"name": "Mijn sensor"}, headers=admin.h())
    assert r.status_code == 200
    devices[1]["friendly_name"] = "Andere naam"
    await feed("bridge/devices", devices)
    assert (await _devices(home_id))[NEW].name == "Mijn sensor"

    # Leaving the network archives it (adapter), joining again brings it back.
    await feed("bridge/event", {"type": "device_leave", "data": {"ieee_address": NEW, "friendly_name": "Andere naam"}})
    d = (await _devices(home_id))[NEW]
    assert d.lifecycle == "archived" and d.diagnostics["archived_by"] == "adapter" and d.online is False
    await feed("bridge/event", {"type": "device_joined", "data": {"friendly_name": "Andere naam", "ieee_address": NEW}})
    d = (await _devices(home_id))[NEW]
    assert d.lifecycle == "active" and d.kind == "zigbee_sensor" and d.name == "Mijn sensor"


async def test_reading_before_inventory_uses_adapter_kind(admin):
    """A reading for an unknown identity creates the device with the adapter's kind (not "device")."""
    from shc_integrations.base import DeviceInfo, Reading

    home_id = await new_home(admin)
    integ = await add_integration(home_id, "zigbee", config={"host": "mqtt"})
    now = datetime.now(UTC)
    info = DeviceInfo("0xabc0000000000001", "zigbee_sensor", "Badkamer", now, model="WSDCGQ11LM")
    w = make_worker()
    attach(w, integ, SimpleNamespace(device_info=lambda ident: info if ident == info.device_identity else None))
    await w.handle_events(integ, [Reading("0xabc0000000000001", "battery", 80, "%", now)])
    await w.handle_events(integ, [Reading("0xabc0000000000002", "temperature", 20.0, "°C", now)])
    devs = await _devices(home_id)
    assert devs["0xabc0000000000001"].kind == "zigbee_sensor" and devs["0xabc0000000000001"].name == "Badkamer"
    assert devs["0xabc0000000000002"].kind == "zigbee_sensor"  # temperature means sensor


# --- items 25 and 27: Hue inventory, groups, scenes, rename/delete -------------------------------


async def test_hue_inventory_groups_scenes_buttons(admin):
    from shc_integrations.hue import HueModel

    inv = hue_inventory()
    home_id = await new_home(admin)
    integ = await add_integration(home_id, "hue", "Hue Bridge", config={"host": "192.168.1.2"})
    model = HueModel()
    model.load(inv.full_resources())
    model.set_v1_rules(inv.V1_RULES)
    w = make_worker()
    attach(w, integ, SimpleNamespace(device_info=lambda r: model.info_for(r, datetime.now(UTC))))
    await w.handle_events(integ, model.snapshot(datetime.now(UTC)))

    devs = await _devices(home_id)
    dimmer = devs[inv.D_DIMMER]
    assert dimmer.kind == "button" and dimmer.name == "Dimmer gang" and dimmer.model == "RWL022"
    assert [b["id"] for b in dimmer.state["buttons"]] == [inv.B1, inv.B2, inv.B3, inv.B4]
    assert all(len(b["id"]) == 36 for b in dimmer.state["buttons"])
    assert set(dimmer.state["bridge_rules"]) == {inv.B1, inv.B2, inv.B4}
    assert "short_release" in dimmer.state["events"]
    assert not [d for d in devs.values() if d.kind == "hue_device"]

    lights = (await admin.get(f"/homes/{home_id}/lights")).json()
    groups = {g["name"]: g for g in lights["groups"]}
    assert lights["groups"][-1]["name"] == "Alle lampen" and lights["groups"][-1]["all_lights"] is True
    assert lights["groups"][-1]["hidden"] is True
    assert "Lichtgroep" not in groups
    living = groups["Woonkamer"]
    assert (living["lights_on"], living["lights_total"], living["reachable"]) == (1, 2, True)
    assert living["scene"] == "Avond"
    scenes = {s["name"]: s for s in lights["scenes"]}
    assert scenes["Avond"]["group_id"] == living["id"] and scenes["Avond"]["active"] is True
    assert living["scene_id"] == scenes["Avond"]["id"]
    bed = groups["Slaapkamer"]
    assert bed["reachable"] is False  # its only lamp has connectivity issues
    lamps = {lamp["name"]: lamp for lamp in lights["lights"]}
    assert lamps["Nachtlamp"]["reachable"] is False and lamps["Plafond"]["reachable"] is True

    # Renamed in the Hue app -> renamed here; a deleted scene disappears.
    evs = model.apply_container(
        {
            "type": "update",
            "id": "c1",
            "data": [{"id": inv.ROOM_LIVING, "type": "room", "metadata": {"name": "Zitkamer"}}],
        }
    )
    await w.handle_events(integ, evs)
    evs = model.apply_container({"type": "delete", "id": "c2", "data": [{"id": inv.S_BRIGHT, "type": "scene"}]})
    await w.handle_events(integ, evs)
    lights = (await admin.get(f"/homes/{home_id}/lights")).json()
    assert "Zitkamer" in {g["name"] for g in lights["groups"]}
    assert "Helder" not in {s["name"] for s in lights["scenes"]}
    assert (await _devices(home_id))[inv.S_BRIGHT].lifecycle == "archived"

    # A group command the bridge answered with errors shows as failed with the bridge's message.
    group_id = uuid.UUID(living["id"])
    async with sessionmaker()() as db:
        db.add(
            CommandRecord(
                home_id=home_id,
                device_id=group_id,
                action="set_group",
                params={"on": True},
                source="user",
                actor="test",
                status="sent",
                detail="Hue Bridge meldt: device (Plafond) has communication issues",
            )
        )
        await db.commit()
    g = next(x for x in (await admin.get(f"/homes/{home_id}/lights")).json()["groups"] if x["id"] == str(group_id))
    assert g["last_command"]["status"] == "failed" and "communication issues" in g["last_command"]["detail"]


# --- item 15: boiler socket relay / switch lock -------------------------------------------------


async def test_protected_socket_relay_and_switch_lock_alerts(admin):
    from shc_integrations.base import StateUpdate

    home_id = await new_home(admin)
    integ = await add_integration(home_id, "homewizard", config={"host": "192.168.1.60", "product_type": "HWE-SKT"})
    sock = await add_device(home_id, "energy_socket", "Boilerstekker", provider="homewizard", stable_identity="SKT01")
    other = await add_device(home_id, "energy_socket", "Wasmachine", provider="homewizard", stable_identity="SKT02")
    async with sessionmaker()() as db:
        role = (
            await db.execute(select(LogicalRole).where(LogicalRole.home_id == home_id, LogicalRole.key == "boiler"))
        ).scalar_one()
        db.add(DeviceBinding(role_id=role.id, device_id=sock.id, effective_from=datetime.now(UTC)))
        await db.commit()
    w = make_worker()
    attach(w, integ, SimpleNamespace())
    now = datetime.now(UTC)

    def upd(ident, **st):
        return StateUpdate(ident, {"relay_state": "ok", **st}, now)

    await w.handle_events(
        integ, [upd("SKT01", relay_on=False, switch_lock=False), upd("SKT02", relay_on=False, switch_lock=False)]
    )
    off = await _open_alert(f"relay_off:{sock.id}")
    lock = await _open_alert(f"switch_lock:{sock.id}")
    assert off is not None and off.severity == "error" and "staat uit" in off.title
    assert lock is not None and lock.severity == "warn" and "Switch lock" in lock.detail
    assert await _open_alert(f"relay_off:{other.id}") is None  # not protected: no alarm
    view = (await admin.get(f"/devices/{sock.id}")).json()
    assert view["details"]["relay"] == {"on": False, "switch_lock": False, "state": "ok", "led_brightness": None}
    assert view["protected"] is True

    await w.handle_events(integ, [upd("SKT01", relay_on=True, switch_lock=True)])
    assert await _open_alert(f"relay_off:{sock.id}") is None
    assert await _open_alert(f"switch_lock:{sock.id}") is None
    # Unsupported state (v2 without the v1 endpoint): no guesses, no alarms.
    await w.handle_events(
        integ, [StateUpdate("SKT01", {"relay_on": None, "switch_lock": None, "relay_state": "unsupported"}, now)]
    )
    assert (await admin.get(f"/devices/{sock.id}")).json()["details"]["relay"]["state"] == "unsupported"


# --- item 25: button presses ---------------------------------------------------------------------


async def test_button_press_dedupe_age_and_climate_step_via_zone_command(admin):
    from shc_integrations.base import ButtonEvent, DeviceInfo

    home_id = await new_home(admin)
    integ = await add_integration(home_id, "hue", config={"host": "192.168.1.2"})
    btn_rid, b1, b2 = (
        "9" * 8 + "-0000-4000-8000-000000000001",
        "1" * 8 + "-0000-4000-8000-000000000001",
        "1" * 8 + "-0000-4000-8000-000000000002",
    )
    group = await add_device(home_id, "light_group", "Woonkamer", provider="hue", state={"on": False})
    unit = await add_device(
        home_id,
        "ac_unit",
        "Airco woonkamer",
        provider="gree",
        state={
            "caps": {"modes": ["off", "heat", "cool"], "min_c": 16, "max_c": 30, "step_c": 1.0},
            "mode": "heat",
            "power": True,
            "target_c": 20,
        },
    )
    zones = {z["name"]: z["id"] for z in (await admin.get(f"/homes/{home_id}/zones")).json()}
    r = await admin.c.patch(
        f"/api/v1/zones/{zones['Woonkamer']}", json={"unit_device_id": str(unit.id)}, headers=admin.h()
    )
    assert r.status_code == 200
    w = make_worker()
    attach(w, integ, SimpleNamespace(device_info=lambda _r: None))
    now = datetime.now(UTC)
    buttons = [
        {"id": b1, "control_id": 1, "type": "button", "events": ["short_release", "long_press"]},
        {"id": b2, "control_id": 2, "type": "button", "events": ["short_release"]},
    ]
    await w.handle_events(
        integ,
        [
            DeviceInfo(
                btn_rid, "button", "Knop hal", now, model="RWL022", extra={"buttons": buttons, "bridge_rules": []}
            )
        ],
    )
    dev = (await _devices(home_id))[btn_rid]
    r = await admin.put(
        f"/buttons/{dev.id}/mappings",
        json=[
            {
                "button": b1,
                "event": "short_release",
                "mode": "lights",
                "action": {"steps": [{"device_id": str(group.id), "action": "toggle"}]},
            },
            {
                "button": b2,
                "event": "short_release",
                "mode": "climate",
                "action": {"steps": [{"zone_id": zones["Woonkamer"], "target_c": 22}]},
            },
        ],
    )
    assert r.status_code == 200, r.text

    press = ButtonEvent(btn_rid, b1, "short_release", now, event_id=f"evt-1:{b1}")
    w.wake_commands.clear()
    await w.handle_events(integ, [press])
    await w.handle_events(integ, [press])  # the same event again (second integration, replay): ignored
    assert w.wake_commands.is_set()
    async with sessionmaker()() as db:
        cmds = (await db.execute(select(CommandRecord).where(CommandRecord.device_id == group.id))).scalars().all()
        assert len(cmds) == 1 and cmds[0].action == "set_group" and cmds[0].params == {"on": True}
        assert cmds[0].source == "button"
        n = (
            await db.execute(select(func.count()).select_from(ProcessedEvent).where(ProcessedEvent.device_id == dev.id))
        ).scalar_one()
        assert n == 1
    # Older than 10 s: ignored (e.g. replay after reconnect).
    old = ButtonEvent(btn_rid, b1, "short_release", now - timedelta(seconds=30), event_id=f"evt-0:{b1}")
    await w.handle_events(integ, [old])
    async with sessionmaker()() as db:
        assert (
            len((await db.execute(select(CommandRecord).where(CommandRecord.device_id == group.id))).scalars().all())
            == 1
        )

    # Climate step: through request_zone_command (manual override, zone log, outdoor-unit checks).
    await w.handle_events(integ, [ButtonEvent(btn_rid, b2, "short_release", now, event_id=f"evt-2:{b2}")])
    async with sessionmaker()() as db:
        cmd = (await db.execute(select(CommandRecord).where(CommandRecord.device_id == unit.id))).scalar_one()
        assert cmd.action == "set_hvac" and cmd.params == {"target_c": 22.0} and cmd.source == "button"
        z = await db.get(ClimateZone, uuid.UUID(zones["Woonkamer"]))
        assert z.control == "manual" and z.override_source == "button" and z.pending_command_id == cmd.id
    # A refused climate step (unit off, no mode) is logged on the zone, nothing queued.
    async with sessionmaker()() as db:
        u = await db.get(Device, unit.id)
        u.state = {**u.state, "power": False, "mode": "off"}
        await db.execute(
            CommandRecord.__table__.update().where(CommandRecord.device_id == unit.id).values(status="confirmed")
        )
        await db.commit()
    await w.handle_events(integ, [ButtonEvent(btn_rid, b2, "short_release", now, event_id=f"evt-3:{b2}")])
    async with sessionmaker()() as db:
        assert (
            len((await db.execute(select(CommandRecord).where(CommandRecord.device_id == unit.id))).scalars().all())
            == 1
        )
    log = (await admin.get(f"/zones/{zones['Woonkamer']}/log")).json()
    assert any("Kies eerst een modus" in x["message"] for x in log)


async def test_unknown_button_device_from_press_is_kind_button(admin):
    from shc_integrations.base import ButtonEvent

    home_id = await new_home(admin)
    integ = await add_integration(home_id, "hue", config={"host": "192.168.1.2"})
    w = make_worker()
    attach(w, integ, SimpleNamespace())
    rid = "7" * 8 + "-0000-4000-8000-000000000001"
    await w.handle_events(integ, [ButtonEvent(rid, rid, "initial_press", datetime.now(UTC), event_id="e:1")])
    d = (await _devices(home_id))[rid]
    assert d.kind == "button" and d.state["last_event"]["event"] == "initial_press"
    assert await fetch(Device, d.id) is not None
