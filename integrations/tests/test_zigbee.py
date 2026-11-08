import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import load

from shc_integrations.base import AdapterError, Cap, Quality, Reading, StateUpdate
from shc_integrations.zigbee import ZigbeeAdapter, is_stale, normalize_device_message

IEEE = "0x00158d0001a2b3c4"
NOW = datetime(2026, 11, 8, 13, 40, tzinfo=UTC)


def make():
    published = []

    async def pub(topic, payload):
        published.append((topic, payload))

    a = ZigbeeAdapter({"host": "mqtt"}, publisher=pub)
    a.handle_message("zigbee2mqtt/bridge/devices", json.dumps(load("zigbee/bridge_devices.json")), NOW)
    return a, published


def test_normalize_sensor_message():
    rs = {r.metric: r for r in normalize_device_message(IEEE, load("zigbee/sensor_message.json"), NOW)}
    assert rs["temperature"].value == 20.62 and rs["temperature"].unit == "°C"
    assert rs["humidity"].value == 54.21
    assert rs["battery"].value == 87
    assert rs["link_quality"].value == 120
    assert rs["temperature"].observed_at == datetime(2026, 11, 8, 13, 39, tzinfo=UTC)
    assert "power_outage_count" not in rs


def test_implausible_value_marked_missing():
    rs = normalize_device_message(IEEE, {"temperature": 150}, NOW)
    assert rs[0].value is None and rs[0].quality == Quality.MISSING


async def test_devices_and_messages_by_friendly_name():
    a, _ = make()
    devs = await a.discover()
    assert [d.stable_identity for d in devs] == [IEEE]  # coordinator excluded
    assert devs[0].kind == "zigbee_sensor" and devs[0].model == "WSDCGQ11LM"
    assert await a.get_capabilities(IEEE) == {Cap.TEMPERATURE, Cap.HUMIDITY, Cap.BATTERY, Cap.LINK_QUALITY}
    ev = a.handle_message("zigbee2mqtt/woonkamer_sensor", json.dumps(load("zigbee/sensor_message.json")), NOW)
    assert all(isinstance(e, Reading) and e.device_identity == IEEE for e in ev)
    assert a.handle_message("zigbee2mqtt/woonkamer_sensor/set", "{}", NOW) == []
    av = a.handle_message("zigbee2mqtt/woonkamer_sensor/availability", '{"state":"offline"}', NOW)
    assert isinstance(av[0], StateUpdate) and av[0].online is False


async def test_bridge_state_health():
    a, _ = make()
    assert (await a.health()).state.value == "warn"
    a.handle_message("zigbee2mqtt/bridge/state", '{"state":"offline"}', NOW)
    assert (await a.health()).state.value == "error"
    a.handle_message("zigbee2mqtt/bridge/state", "online", NOW)  # legacy plain payload
    a.last_seen[IEEE] = datetime.now(UTC)
    assert (await a.health()).state.value == "ok"


async def test_bridge_event_join():
    a, _ = make()
    ev = a.handle_message(
        "zigbee2mqtt/bridge/event",
        json.dumps(
            {
                "type": "device_interview",
                "data": {"friendly_name": "0xabc", "ieee_address": "0xabc", "status": "successful"},
            }
        ),
        NOW,
    )
    assert ev[0].state["zigbee_event"] == "device_interview"


async def test_permit_join_and_rename_publish():
    a, published = make()
    res = await a.pair(seconds=120)
    assert res.retry_after_s == 120
    await a.rename(IEEE, "Woonkamer")
    assert published == [
        ("zigbee2mqtt/bridge/request/permit_join", {"time": 120}),
        ("zigbee2mqtt/bridge/request/device/rename", {"from": IEEE, "to": "Woonkamer"}),
    ]
    with pytest.raises(AdapterError):
        await a.rename(IEEE, "a/b")
    with pytest.raises(AdapterError):
        await a.permit_join(999)


def test_is_stale():
    assert is_stale(None, NOW)
    assert not is_stale(NOW - timedelta(minutes=50), NOW, 3600)
    assert is_stale(NOW - timedelta(minutes=61), NOW, 3600)
