"""Zigbee adapter against the contract kit (MQTT publishing captured; unreachable = real closed port)."""

import json
from datetime import UTC, datetime

import pytest
from conftest import load
from contract_kit import AdapterContract, ContractEnv
from fake_http import closed_tcp_port

from shc_integrations.base import Cap, Command
from shc_integrations.zigbee import COORDINATOR_IDENTITY, ZigbeeAdapter

IEEE = "0x00158d0001a2b3c4"


class TestZigbeeContract(AdapterContract):
    @pytest.fixture
    async def env(self):
        published = []

        async def publish(topic, payload):
            published.append((topic, payload))

        adapter = ZigbeeAdapter({"host": "mqtt"}, publisher=publish)
        adapter.connected = True  # as after subscribe() connected to the broker
        adapter.first_connected_at = adapter.connected_since = datetime.now(UTC)
        events = adapter.handle_message("zigbee2mqtt/bridge/state", '{"state":"online"}')
        events += adapter.handle_message("zigbee2mqtt/bridge/devices", json.dumps(load("zigbee/bridge_devices.json")))
        events += adapter.handle_message("zigbee2mqtt/woonkamer_sensor/availability", '{"state":"online"}')
        events += adapter.handle_message("zigbee2mqtt/woonkamer_sensor", json.dumps(load("zigbee/sensor_message.json")))
        events += adapter.handle_message("zigbee2mqtt/woonkamer_sensor", json.dumps({"temperature": "n/a"}))
        yield ContractEnv(
            adapter=adapter,
            device_identity=IEEE,
            supported_command=Command(COORDINATOR_IDENTITY, "permit_join", {"seconds": 60}),
            expect_confirmed=False,  # Zigbee2MQTT gives no readback here: never claim confirmation
            unsupported_command=Command(IEEE, "set_light", {"on": True}),
            unreachable=ZigbeeAdapter({"host": "127.0.0.1", "port": closed_tcp_port(), "timeout_s": 2}),
            unconfigured=ZigbeeAdapter({}),
            expected_caps={Cap.TEMPERATURE, Cap.HUMIDITY, Cap.BATTERY, Cap.LINK_QUALITY},
            polls=False,
            sample_events=events,
        )
