"""Hue adapter against the contract kit, with a fake bridge on real HTTPS (pinned certificate)."""

import ssl

import pytest
from conftest import load
from contract_kit import AdapterContract, ContractEnv
from fake_http import FakeHttpDevice, closed_tcp_port
from hue_inventory import L1, full_resources
from test_tls import make_cert

from shc_integrations.base import Cap, Command
from shc_integrations.hue import HueAdapter

KEY = load("hue/pair_success.json")[0]["success"]["username"]


class TestHueContract(AdapterContract):
    @pytest.fixture
    async def env(self, tmp_path):
        cp, kp, fp = make_cert(tmp_path)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cp, kp)
        light = {"on": True, "brightness": 60.0}
        obey = {"on": True}

        def put_light(body, _headers):
            if obey["on"]:
                light["on"] = body.get("on", {}).get("on", light["on"])
                light["brightness"] = body.get("dimming", {}).get("brightness", light["brightness"])
            return 200, {"data": [{"rid": L1, "rtype": "light"}], "errors": []}

        def get_light(_b, _h):
            data = {
                "id": L1,
                "type": "light",
                "on": {"on": light["on"]},
                "dimming": {"brightness": light["brightness"]},
            }
            return 200, {"errors": [], "data": [data]}

        routes = {
            ("GET", "/api/0/config"): lambda b, h: (200, load("hue/config.json")),
            ("POST", "/api"): lambda b, h: (200, load("hue/pair_success.json")),
            ("GET", "/clip/v2/resource"): lambda b, h: (200, {"errors": [], "data": full_resources()}),
            ("GET", f"/api/{KEY}/rules"): lambda b, h: (200, {}),
            ("GET", "/clip/v2/resource/bridge"): lambda b, h: (200, {"data": [{"bridge_id": "ecb5fafffe000001"}]}),
            ("PUT", f"/clip/v2/resource/light/{L1}"): put_light,
            ("GET", f"/clip/v2/resource/light/{L1}"): get_light,
        }
        async with FakeHttpDevice(routes, ssl_context=ctx) as dev:
            adapter = HueAdapter({}, {})  # real pinned TLS client with TCP keepalive
            paired = await adapter.pair(dev.host)
            assert paired.credentials["cert_fingerprint"] == fp
            yield ContractEnv(
                adapter=adapter,
                device_identity=L1,
                supported_command=Command(L1, "set_light", {"on": False, "brightness": 30}),
                unsupported_command=Command(L1, "set_hvac", {"power": True}),
                unreachable=HueAdapter({"host": f"127.0.0.1:{closed_tcp_port()}"}, dict(paired.credentials)),
                unconfigured=HueAdapter({}, {}),
                stop_obeying=lambda: obey.update(on=False),
                expected_caps={Cap.POWER, Cap.BRIGHTNESS},
                discovered=paired.device,
            )
            await adapter.close()
