"""HomeWizard adapter against the contract kit, with a fake Energy Socket on real HTTP."""

import pytest
from conftest import load
from contract_kit import AdapterContract, ContractEnv
from fake_http import FakeHttpDevice, closed_tcp_port

from shc_integrations.base import Cap, Command
from shc_integrations.homewizard import HomeWizardAdapter

SERIAL = "3c39e7000002"
INFO = {
    "product_name": "Energy Socket",
    "product_type": "HWE-SKT",
    "serial": SERIAL,
    "firmware_version": "3.03",
    "api_version": "v1",
}


class TestHomeWizardContract(AdapterContract):
    @pytest.fixture
    async def env(self):
        state = {"power_on": True, "switch_lock": False, "brightness": 255}
        obey = {"on": True}

        def put_state(body, _headers):
            if obey["on"]:
                state.update(body)
            return 200, dict(body)

        routes = {
            ("GET", "/api"): lambda b, h: (200, INFO),
            ("GET", "/api/v1/data"): lambda b, h: (200, load("homewizard/v1_data_socket.json")),
            ("GET", "/api/v1/state"): lambda b, h: (200, dict(state)),
            ("PUT", "/api/v1/state"): put_state,
        }
        async with FakeHttpDevice(routes) as dev:
            # Switching is denied by default; only this test socket is allowed by the injected policy hook.
            adapter = HomeWizardAdapter({"min_poll_interval_s": 0}, relay_allowed=lambda ident: ident == SERIAL)
            paired = await adapter.pair(dev.host)
            yield ContractEnv(
                adapter=adapter,
                device_identity=SERIAL,
                supported_command=Command(SERIAL, "set_relay", {"on": False}),
                unsupported_command=Command(SERIAL, "set_hvac", {"power": True}),
                unreachable=HomeWizardAdapter(
                    {"host": f"127.0.0.1:{closed_tcp_port()}", "serial": SERIAL, "product_type": "HWE-SKT"},
                    relay_allowed=lambda ident: True,
                ),
                unconfigured=HomeWizardAdapter({}),
                stop_obeying=lambda: obey.update(on=False),
                expected_caps={Cap.MEASURED_POWER, Cap.ENERGY_IMPORT, Cap.ENERGY_EXPORT, Cap.RELAY},
                discovered=paired.device,
            )
            await adapter.close()
