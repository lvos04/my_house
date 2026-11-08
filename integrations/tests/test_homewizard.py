import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from conftest import load

from shc_integrations.base import (
    Cap,
    Command,
    PairingInstructions,
    PairResult,
    PolicyDenied,
    Reading,
    StateUpdate,
)
from shc_integrations.homewizard import HomeWizardAdapter, capabilities_for, normalize_v1, normalize_v2

TZ = ZoneInfo("Europe/Amsterdam")


async def fp(_h):
    return "cd" * 32


def test_capability_detection():
    assert Cap.RELAY in capabilities_for("HWE-SKT")
    assert Cap.GAS in capabilities_for("HWE-P1") and Cap.RELAY not in capabilities_for("HWE-P1")
    assert capabilities_for("HWE-KWH1") == {Cap.MEASURED_POWER, Cap.ENERGY_IMPORT, Cap.ENERGY_EXPORT}
    assert capabilities_for("HWE-XYZ") == set()


def test_normalize_v1_p1_with_gas_timestamp():
    at = datetime(2026, 11, 8, 13, 0, tzinfo=UTC)
    rs = {r.metric: r for r in normalize_v1("p1", load("homewizard/v1_data_p1.json"), at, TZ)}
    assert rs["energy_import_kwh"].value == 13779.338
    assert rs["power_w"].value == -543
    assert rs["energy_import_t2_kwh"].value == 2948.827
    gas = rs["gas_m3"]
    assert gas.value == 2569.646
    # 26-11-08 14:00:10 local (CET, UTC+1) -> 13:00:10Z
    assert gas.observed_at == datetime(2026, 11, 8, 13, 0, 10, tzinfo=UTC)
    assert rs["power_w"].event_id == f"homewizard:p1:power_w:{at.isoformat()}"


def test_normalize_v2_uses_device_timestamp():
    at = datetime(2026, 11, 8, 13, 15, tzinfo=UTC)
    rs = {r.metric: r for r in normalize_v2("p1", load("homewizard/v2_measurement_p1.json"), at, TZ)}
    assert rs["power_w"].value == 1240
    assert rs["energy_import_kwh"].observed_at == datetime(2026, 11, 8, 13, 12, 34, tzinfo=UTC)
    assert rs["gas_m3"].observed_at == datetime(2026, 11, 8, 13, 0, tzinfo=UTC)


@respx.mock
async def test_v1_socket_poll_and_relay_blocked_by_default(plain_factory):
    respx.get("http://10.0.0.5/api/v1/data").respond(json=load("homewizard/v1_data_socket.json"))
    respx.get("http://10.0.0.5/api/v1/state").respond(json=load("homewizard/v1_state_socket.json"))
    put = respx.put("http://10.0.0.5/api/v1/state").respond(json={"power_on": False})
    a = HomeWizardAdapter(
        {"host": "10.0.0.5", "serial": "3c39e7000002", "product_type": "HWE-SKT"}, http_factory=plain_factory
    )
    ev = await a.poll()
    assert any(isinstance(e, Reading) and e.metric == "power_w" and e.value == 1987.2 for e in ev)
    assert any(isinstance(e, StateUpdate) and e.state["relay_on"] is True for e in ev)
    for on in (True, False):
        with pytest.raises(PolicyDenied):
            await a.execute(Command("3c39e7000002", "set_relay", {"on": on}))
    assert not put.called


@respx.mock
async def test_relay_allowed_only_via_policy_hook(plain_factory):
    respx.put("http://10.0.0.6/api/v1/state").respond(json={"power_on": False})
    respx.get("http://10.0.0.6/api/v1/state").respond(json={"power_on": False})
    a = HomeWizardAdapter(
        {"host": "10.0.0.6", "serial": "s2", "product_type": "HWE-SKT"},
        relay_allowed=lambda ident: ident == "s2",
        http_factory=plain_factory,
    )
    res = await a.execute(Command("s2", "set_relay", {"on": False}))
    assert res.confirmed
    with pytest.raises(PolicyDenied):
        await a.execute(Command("boiler", "set_relay", {"on": False}))


@respx.mock
async def test_pair_v1_socket(plain_factory):
    respx.get("http://10.0.0.5/api").respond(json=load("homewizard/v1_api_socket.json"))
    a = HomeWizardAdapter({}, http_factory=plain_factory, fingerprint_fetcher=fp)
    res = await a.pair("10.0.0.5")
    assert isinstance(res, PairResult) and res.credentials == {}
    assert a.config["api_version"] == "v1" and res.device.kind == "energy_socket"


@respx.mock
async def test_pair_v2_requires_button_then_token(plain_factory):
    respx.get("http://10.0.0.7/api").respond(json=load("homewizard/v2_api_kwh.json"))
    user = respx.post("https://10.0.0.7/api/user")
    user.side_effect = [
        respx.MockResponse(403, json={"error": "user:creation-not-enabled"}),
        respx.MockResponse(200, json={"token": "DOCEXAMPLETOKEN"}),
    ]
    a = HomeWizardAdapter({}, http_factory=plain_factory, fingerprint_fetcher=fp)
    assert isinstance(await a.pair("10.0.0.7"), PairingInstructions)
    res = await a.pair("10.0.0.7")
    assert res.credentials == {"token": "DOCEXAMPLETOKEN", "cert_fingerprint": "cd" * 32}
    assert json.loads(user.calls.last.request.content) == {"name": "local/smart_home_control"}

    m = respx.get("https://10.0.0.7/api/measurement").respond(json=load("homewizard/v2_measurement_p1.json"))
    await a.poll()
    h = m.calls.last.request.headers
    assert h["authorization"] == "Bearer DOCEXAMPLETOKEN" and h["x-api-version"] == "2"


@respx.mock
async def test_local_api_disabled_message(plain_factory):
    respx.get("http://10.0.0.9/api").respond(status_code=403)
    from shc_integrations.base import AdapterError

    with pytest.raises(AdapterError, match="Lokale API"):
        await HomeWizardAdapter({}, http_factory=plain_factory).probe("10.0.0.9")


@respx.mock
async def test_pair_falls_back_to_v1_on_old_firmware(plain_factory):
    """A v2-capable product on firmware without the HTTPS endpoint pairs over v1."""
    respx.get("http://10.0.0.8/api").respond(json=load("homewizard/v2_api_kwh.json"))
    respx.post("https://10.0.0.8/api/user").mock(side_effect=httpx.ConnectError("refused"))
    a = HomeWizardAdapter({}, http_factory=plain_factory, fingerprint_fetcher=fp)
    res = await a.pair("10.0.0.8")
    assert isinstance(res, PairResult) and res.credentials == {}
    assert a.config["api_version"] == "v1"
