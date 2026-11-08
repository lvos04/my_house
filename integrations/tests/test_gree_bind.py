"""Items 9, 29 and the small Gree protocol item: bind order, diagnostics, capabilities, TemSen/TemRec/swing."""

import json

import pytest
from fake_gree import DEVICE_KEY, MAC, FakeGreeUnit, Sleeps, closed_udp_port, start_unit

from shc_integrations.base import AdapterError, Command, DeviceOffline, PairResult, Reading, StateUpdate, Unsupported
from shc_integrations.gree import GreeAdapter, decode_status, encode_command
from shc_integrations.gree import protocol as p


def adapter(port, **cfg):
    return GreeAdapter({"port": port, "timeout_s": 0.3, **cfg}, sleep=Sleeps())


async def test_scan_v1_but_bind_only_v2_pairs_with_v2():
    """Newer modules answer the scan in the old format but only accept the new encryption."""
    unit, transport, port = await start_unit(version="v2", scan_version="v1", bind_versions={"v2"})
    try:
        a = adapter(port)
        res = await a.pair("127.0.0.1")
        assert isinstance(res, PairResult) and res.credentials == {"key": DEVICE_KEY}
        assert unit.bind_requests == ["v1", "v1", "v2"]  # old first (2 tries), then new
        assert a.config["encryption"] == "v2"  # remembered: the API stores adapter.config
        assert res.device.extra["encryption"] == "v2" and res.device.extra["scan_encryption"] == "v1"
        diag = a.diagnostics()
        assert diag["bind_attempts"] == [
            {"encryption": "v1", "result": "geen antwoord"},
            {"encryption": "v2", "result": "ok"},
        ]
        assert diag["stage"] == "paired" and DEVICE_KEY not in json.dumps(diag)
        st = next(e for e in await a.poll() if isinstance(e, StateUpdate))
        assert st.state["mode"] == "heat"
    finally:
        transport.close()


async def test_classic_v1_unit_binds_on_first_try():
    unit, transport, port = await start_unit(version="v1")
    try:
        a = adapter(port)
        await a.pair("127.0.0.1")
        assert unit.bind_requests == ["v1"] and a.config["encryption"] == "v1"
    finally:
        transport.close()


async def test_forced_encryption_only_tries_that_one():
    unit, transport, port = await start_unit(version="v2")
    try:
        a = adapter(port)
        await a.pair("127.0.0.1", encryption="v2")
        assert unit.bind_requests == ["v2"]
        with pytest.raises(AdapterError):
            await adapter(port).pair("127.0.0.1", encryption="v3")
    finally:
        transport.close()


async def test_bind_failure_is_unsupported_with_scan_diagnostics():
    unit, transport, port = await start_unit(version="v1", bind_versions=set())
    try:
        a = adapter(port)
        with pytest.raises(Unsupported) as ei:
            await a.pair("127.0.0.1")
        err = ei.value
        assert err.code == "unsupported"
        d = err.diagnostics
        assert d["stage"] == "bind_failed"
        assert d["scan"]["model"] == "gree" and d["scan"]["ver"] == "V1.2.1" and d["scan"]["mac"] == MAC
        assert d["scan"]["mid"] == "10001" and d["firmware"] == "V1.2.1"
        assert [x["encryption"] for x in d["bind_attempts"]] == ["v1", "v2"]
        assert all(x["result"] == "geen antwoord" for x in d["bind_attempts"])
        json.dumps(d)  # storable by the API
    finally:
        transport.close()


async def test_no_scan_answer_keeps_a_diagnostics_trail():
    port = await closed_udp_port()
    a = adapter(port)
    with pytest.raises(DeviceOffline) as ei:
        await a.pair("127.0.0.1")
    assert ei.value.diagnostics["stage"] == "scan_failed" and ei.value.diagnostics["host"] == "127.0.0.1"


class GarbledStatusUnit(FakeGreeUnit):
    """Binds fine, but answers status requests with a packet nobody can decrypt."""

    def datagram_received(self, data, addr):
        msg = json.loads(data)
        if msg.get("t") == "scan" or msg.get("i") == 1:
            return super().datagram_received(data, addr)
        self.transport.sendto(json.dumps({"t": "pack", "pack": p.encrypt_v1({"t": "x"}, b"0" * 16)}).encode(), addr)


async def test_status_failure_after_bind_is_unsupported():
    import asyncio

    loop = asyncio.get_running_loop()
    unit = GarbledStatusUnit()
    transport, _ = await loop.create_datagram_endpoint(lambda: unit, local_addr=("127.0.0.1", 0))
    try:
        a = adapter(transport.get_extra_info("sockname")[1])
        with pytest.raises(Unsupported) as ei:
            await a.pair("127.0.0.1")
        assert ei.value.diagnostics["stage"] == "status_failed"
        assert ei.value.diagnostics["bind_attempts"][-1]["result"] == "ok"
        assert a.credentials == {}  # nothing half-paired
    finally:
        transport.close()


async def test_capabilities_after_pairing_follow_contract_c1():
    unit, transport, port = await start_unit()
    try:
        a = adapter(port)
        res = await a.pair("127.0.0.1")
        caps = a.climate_capabilities(MAC)
        assert caps == {
            "modes": ["off", "auto", "cool", "dry", "fan", "heat"],
            "min_c": 16.0,
            "max_c": 30.0,
            "step_c": 1.0,
            "fan": True,
            "swing": True,
        }
        assert res.device.extra["caps"] == caps
        st = next(e for e in await a.poll() if isinstance(e, StateUpdate))
        assert st.state["caps"] == caps  # the worker merges this into Device.state["caps"]
    finally:
        transport.close()


async def test_half_degree_units_only_when_configured():
    unit, transport, port = await start_unit()
    try:
        a = adapter(port, half_degrees=True)
        await a.pair("127.0.0.1")
        assert a.climate_capabilities(MAC)["step_c"] == 0.5
        out = await a.execute(Command(MAC, "set_hvac", {"target_c": 21.5}))
        assert out.confirmed and out.state["target_c"] == 21.5
        opt, vals = unit.commands[-1]
        assert dict(zip(opt, vals, strict=True)) == {"TemUn": 0, "SetTem": 21, "TemRec": 1}
    finally:
        transport.close()


async def test_whole_degree_unit_rejects_half_degree_before_sending():
    unit, transport, port = await start_unit()
    try:
        a = adapter(port)
        await a.pair("127.0.0.1")
        with pytest.raises(AdapterError, match="hele graden"):
            await a.execute(Command(MAC, "set_hvac", {"target_c": 21.5}))
        assert unit.commands == []
    finally:
        transport.close()


async def test_unit_without_room_sensor_reports_no_temperature():
    unit, transport, port = await start_unit(state={"TemSen": 0})
    try:
        a = adapter(port)
        await a.pair("127.0.0.1")
        events = await a.poll()
        st = next(e for e in events if isinstance(e, StateUpdate)).state
        assert st["indoor_c"] is None and st["indoor_sensor"] is False
        assert not any(isinstance(e, Reading) for e in events)  # no fake 0 °C, no noise
        assert "temperature" not in {c.value for c in await a.get_capabilities(MAC)}
    finally:
        transport.close()


def test_temrec_is_not_half_a_degree_in_celsius():
    cols = ["SetTem", "TemUn", "TemRec"]
    assert decode_status(cols, [20, 0, 1])["target_c"] == 20
    assert decode_status(cols, [20, 0, 1], half_degrees=True)["target_c"] == 20.5
    f = decode_status(cols, [20, 1, 1], half_degrees=True)
    assert f["target_c"] == 20 and f["display_unit"] == "F"  # TemRec = °F rounding bit


@pytest.mark.parametrize(
    ("raw", "swing", "louver"),
    [
        (0, False, "default"),
        (1, True, "swing_full"),
        (2, False, "fixed_upper"),
        (4, False, "fixed_middle"),
        (6, False, "fixed_lower"),
        (9, True, "swing_middle"),
        (11, True, "swing_upper"),
    ],
)
def test_fixed_louver_is_not_swing(raw, swing, louver):
    st = decode_status(["SwUpDn"], [raw])
    assert st["swing"] is swing and st["louver"] == louver


def test_encode_whole_degrees_by_default():
    assert encode_command({"target_c": 22}) == (["TemUn", "SetTem"], [0, 22])
    assert encode_command({"target_c": 22.5}, half_degrees=True) == (["TemUn", "SetTem", "TemRec"], [0, 22, 1])
    with pytest.raises(AdapterError):
        encode_command({"target_c": 22.3}, half_degrees=True)


async def test_health_has_meaningful_fields():
    unit, transport, port = await start_unit()
    try:
        a = adapter(port)
        assert (await a.health()).state.value == "not_configured"
        await a.pair("127.0.0.1")
        h = await a.health()
        assert h.state.value == "ok" and h.extra["encryption"] == "v1" and h.extra["last_ok_at"]
        assert h.extra["consecutive_failures"] == 0 and h.extra["compatibility"] == "untested"
    finally:
        transport.close()
    a._last_ok_at = None  # force a real check against the now silent address
    h = await a.health()
    assert h.state.value == "error" and h.code == "device_offline"
    assert h.extra["consecutive_failures"] == 1 and h.extra["last_error"]
