"""Item 8 and 28 (adapter side): pairing sequence, device kinds/names, coordinator status, health fields."""

import json
import socket
from datetime import UTC, datetime, timedelta

import pytest
from conftest import load

from shc_integrations.base import DeviceInfo, Reading, StateUpdate, should_upgrade_kind
from shc_integrations.zigbee import COORDINATOR_IDENTITY, ZigbeeAdapter, expose_properties, kind_from_definition

NOW = datetime(2026, 11, 8, 13, 40, tzinfo=UTC)
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


def msg(a, topic, payload, at=NOW):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return a.handle_message(f"zigbee2mqtt/{topic}", raw, at)


def infos(events):
    return [e for e in events if isinstance(e, DeviceInfo)]


def states(events):
    return [e for e in events if isinstance(e, StateUpdate)]


def test_join_interview_availability_measurement_sequence():
    """Replays what Zigbee2MQTT sends while pairing a new sensor."""
    a = ZigbeeAdapter({"host": "mqtt"})
    joined = msg(a, "bridge/event", {"type": "device_joined", "data": {"friendly_name": NEW, "ieee_address": NEW}})
    first = infos(joined)[0]
    assert first.device_identity == NEW and first.kind == "zigbee_device"
    assert first.extra["interview_completed"] is False
    assert states(joined)[0].state["zigbee_event"] == "device_joined"

    started = msg(
        a,
        "bridge/event",
        {"type": "device_interview", "data": {"friendly_name": NEW, "ieee_address": NEW, "status": "started"}},
    )
    assert infos(started) == []  # nothing new learned
    assert states(started)[0].state["status"] == "started"

    done = msg(
        a,
        "bridge/event",
        {
            "type": "device_interview",
            "data": {
                "friendly_name": NEW,
                "ieee_address": NEW,
                "status": "successful",
                "supported": True,
                "definition": DEFINITION,
            },
        },
    )
    info = infos(done)[0]
    assert info.kind == "zigbee_sensor" and info.model == "WSDCGQ11LM"
    assert info.name == "Aqara WSDCGQ11LM (e2d3)"  # friendly_name is still the IEEE address
    assert should_upgrade_kind(first.kind, info.kind)

    # The user renamed it in the app; Zigbee2MQTT republishes the retained device list.
    devices = [
        {"ieee_address": "0x00124b0000000000", "type": "Coordinator", "friendly_name": "Coordinator"},
        {
            "ieee_address": NEW,
            "type": "EndDevice",
            "friendly_name": "slaapkamer/sensor",
            "interview_completed": True,
            "supported": True,
            "definition": DEFINITION,
        },
    ]
    listed = msg(a, "bridge/devices", devices)
    by_id = {i.device_identity: i for i in infos(listed)}
    assert by_id[NEW].name == "slaapkamer/sensor" and by_id[NEW].kind == "zigbee_sensor"
    assert by_id[COORDINATOR_IDENTITY].kind == "zigbee_bridge"
    assert by_id[COORDINATOR_IDENTITY].extra["coordinator_ieee"] == "0x00124b0000000000"
    assert "0x00124b0000000000" not in by_id  # the coordinator is never a separate device

    av = msg(a, "slaapkamer/sensor/availability", {"state": "online"})
    assert av[0].device_identity == NEW and av[0].online is True
    readings = msg(a, "slaapkamer/sensor", {"temperature": 19.5, "humidity": 48, "battery": 100})
    assert {r.metric for r in readings} == {"temperature", "humidity", "battery"}
    assert all(isinstance(r, Reading) and r.device_identity == NEW for r in readings)
    assert msg(a, "slaapkamer/sensor/set", {"x": 1}) == []
    assert a.device_info(NEW).kind == "zigbee_sensor"


@pytest.mark.parametrize("payload", ['{"state":"offline"}', "offline", '"offline"'])
def test_bridge_state_maps_to_coordinator_identity(payload):
    a = ZigbeeAdapter({"host": "mqtt"})
    ev = a.handle_message("zigbee2mqtt/bridge/state", payload, NOW)
    assert len(ev) == 1 and ev[0].device_identity == COORDINATOR_IDENTITY
    assert ev[0].state == {"bridge_state": "offline", "mqtt_connected": True} and ev[0].online is False
    on = a.handle_message("zigbee2mqtt/bridge/state", '{"state":"online"}', NOW)
    assert on[0].online is True and a.bridge_state == "online"
    assert a.handle_message("zigbee2mqtt/bridge/state", "", NOW) == []


def test_bridge_info_describes_coordinator():
    a = ZigbeeAdapter({"host": "mqtt"})
    ev = msg(
        a,
        "bridge/info",
        {
            "version": "2.6.0",
            "coordinator": {"ieee_address": "0x00124b0000000000", "type": "EmberZNet", "meta": {"revision": "7.4.4"}},
            "permit_join": True,
            "permit_join_end": 1762609300000,
        },
    )
    info = ev[0]
    assert info.device_identity == COORDINATOR_IDENTITY and info.name is None
    assert info.model == "EmberZNet" and info.firmware == "7.4.4"
    assert info.extra["zigbee2mqtt_version"] == "2.6.0" and info.extra["permit_join"] is True


def test_device_leave_removes_device():
    a = ZigbeeAdapter({"host": "mqtt"})
    a.handle_message("zigbee2mqtt/bridge/devices", json.dumps(load("zigbee/bridge_devices.json")), NOW)
    ev = msg(a, "bridge/event", {"type": "device_leave", "data": {"ieee_address": "0x00158d0001a2b3c4"}})
    assert infos(ev)[0].removed is True
    assert states(ev)[0].online is False
    assert msg(a, "woonkamer_sensor", {"temperature": 20}) == []  # name no longer known


def test_device_list_change_reports_removed_devices():
    a = ZigbeeAdapter({"host": "mqtt"})
    a.handle_message("zigbee2mqtt/bridge/devices", json.dumps(load("zigbee/bridge_devices.json")), NOW)
    ev = msg(a, "bridge/devices", [])
    assert [i.device_identity for i in infos(ev) if i.removed] == ["0x00158d0001a2b3c4"]


def test_nested_exposes_and_kinds():
    climate = {"exposes": [{"type": "climate", "features": [{"type": "numeric", "property": "local_temperature"}]}]}
    assert expose_properties(climate["exposes"]) == {"local_temperature"}
    assert kind_from_definition(climate) == "zigbee_device"
    nested = {"exposes": [{"type": "composite", "features": [{"features": [{"property": "humidity"}]}]}]}
    assert kind_from_definition(nested) == "zigbee_sensor"
    assert kind_from_definition(None) == "zigbee_device"


def test_kind_upgrade_rules():
    assert should_upgrade_kind("device", "zigbee_sensor")
    assert should_upgrade_kind("zigbee_device", "zigbee_sensor")
    assert not should_upgrade_kind("zigbee_sensor", "zigbee_device")  # never downgrade
    assert not should_upgrade_kind("light", "button")
    assert not should_upgrade_kind("device", None)


async def test_health_reports_mqtt_and_bridge_separately():
    a = ZigbeeAdapter({"host": "mqtt"})
    h = await a.health()
    assert h.state.value == "warn" and h.extra["mqtt_connected"] is False and h.extra["bridge_online"] is None
    a.mqtt_error = "Connection refused"
    h = await a.health()
    assert h.state.value == "error" and h.code == "mqtt_disconnected"

    a.connected, a.mqtt_error = True, None
    a.first_connected_at = datetime.now(UTC)
    a.handle_message("zigbee2mqtt/bridge/state", '{"state":"offline"}')
    h = await a.health()
    assert h.state.value == "error" and h.code == "bridge_offline"
    assert h.extra["mqtt_connected"] is True and h.extra["bridge_online"] is False

    a.handle_message("zigbee2mqtt/bridge/state", '{"state":"online"}')
    a.handle_message("zigbee2mqtt/bridge/devices", json.dumps(load("zigbee/bridge_devices.json")))
    h = await a.health()
    assert h.state.value == "ok" and h.extra["bridge_online"] is True and h.extra["stale_devices"] == []
    json.dumps(h.extra)  # serialisable for the API

    # Only after the sensor had a full staleness window since we connected does it count as stale.
    a.first_connected_at = datetime.now(UTC) - timedelta(hours=2)
    h = await a.health()
    assert h.state.value == "warn" and h.extra["stale_devices"] == ["0x00158d0001a2b3c4"]
    a.last_seen["0x00158d0001a2b3c4"] = datetime.now(UTC)
    assert (await a.health()).state.value == "ok"


async def test_subscribe_reports_unreachable_broker_once():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    class Stop(Exception):
        pass

    sleeps = []

    async def sleep(d):
        sleeps.append(d)
        if len(sleeps) >= 2:
            raise Stop

    a = ZigbeeAdapter({"host": "127.0.0.1", "port": port}, sleep=sleep)
    got = []
    with pytest.raises(Stop):
        async for ev in a.subscribe():
            got.append(ev)
    assert len(got) == 1  # one event per status change, not per attempt
    assert got[0].device_identity == COORDINATOR_IDENTITY and got[0].online is False
    assert got[0].state["mqtt_connected"] is False and got[0].state["mqtt_error"]
    h = await a.health()
    assert h.state.value == "error" and h.code == "mqtt_disconnected"


def test_inventory_lists_coordinator_and_devices():
    a = ZigbeeAdapter({"host": "mqtt"})
    a.handle_message("zigbee2mqtt/bridge/devices", json.dumps(load("zigbee/bridge_devices.json")), NOW)
    ids = [i.device_identity for i in a.inventory()]
    assert ids == [COORDINATOR_IDENTITY, "0x00158d0001a2b3c4"]
    assert a.device_info("0x00158d0001a2b3c4").name == "woonkamer_sensor"
    assert a.device_info("0xunknown") is None
