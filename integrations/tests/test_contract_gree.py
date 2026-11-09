"""Gree adapter against the contract kit, with a fake unit on real UDP."""

import pytest
from contract_kit import AdapterContract, ContractEnv
from fake_gree import DEVICE_KEY, MAC, Sleeps, closed_udp_port, start_unit

from shc_integrations.base import Cap, Command
from shc_integrations.gree import GreeAdapter


class TestGreeContract(AdapterContract):
    @pytest.fixture
    async def env(self):
        unit, transport, port = await start_unit(version="v2", scan_version="v1", bind_versions={"v2"})
        adapter = GreeAdapter({"port": port, "timeout_s": 0.3}, sleep=Sleeps())
        paired = await adapter.pair("127.0.0.1")
        dead = await closed_udp_port()
        yield ContractEnv(
            adapter=adapter,
            device_identity=MAC,
            supported_command=Command(MAC, "set_hvac", {"power": True, "mode": "heat", "target_c": 22}),
            unsupported_command=Command(MAC, "set_light", {"on": True}),
            unreachable=GreeAdapter(
                {"host": "127.0.0.1", "port": dead, "timeout_s": 0.2, "mac": MAC, "encryption": "v2"},
                {"key": DEVICE_KEY},
                sleep=Sleeps(),
            ),
            unconfigured=GreeAdapter({}),
            stop_obeying=lambda: setattr(unit, "obey", False),
            expected_caps={Cap.POWER, Cap.HVAC_MODE, Cap.SETPOINT, Cap.FAN, Cap.SWING, Cap.TEMPERATURE},
            discovered=paired.device,
        )
        transport.close()
