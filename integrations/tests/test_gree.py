import asyncio
import json

import pytest

from shc_integrations.base import Command, DeviceOffline, PairingRequired, PairResult, Reading, StateUpdate
from shc_integrations.gree import GreeAdapter, decode_status, decode_temperature, encode_command
from shc_integrations.gree import protocol as p

DEVICE_KEY = "St8Vw1Yz4Bc7Ef0H"
MAC = "f4911e000001"


class FakeGreeUnit(asyncio.DatagramProtocol):
    """Speaks the Gree LAN protocol well enough for contract tests (not a real device)."""

    def __init__(self, version="v1", obey=True):
        self.version = version
        self.obey = obey
        self.bound = False
        self.state = {
            "Pow": 0,
            "Mod": 4,
            "SetTem": 20,
            "TemUn": 0,
            "WdSpd": 0,
            "SwUpDn": 0,
            "TemSen": 61,
            "TemRec": 0,
            "Lig": 1,
            "Quiet": 0,
            "Tur": 0,
        }
        self.commands = []

    def connection_made(self, transport):
        self.transport = transport

    def _reply(self, inner, key, addr):
        out = {"t": "pack", "i": 0, "uid": 0, "cid": MAC, "tcid": ""}
        if self.version == "v2":
            out["pack"], out["tag"] = p.encrypt_v2(inner, key)
        else:
            out["pack"] = p.encrypt_v1(inner, key)
        self.transport.sendto(json.dumps(out).encode(), addr)

    def datagram_received(self, data, addr):
        msg = json.loads(data)
        generic = p.GENERIC_KEY_V2 if self.version == "v2" else p.GENERIC_KEY_V1
        if msg.get("t") == "scan":
            self._reply(
                {
                    "t": "dev",
                    "cid": MAC,
                    "mac": MAC,
                    "mid": "10001",
                    "model": "gree",
                    "name": "unit1",
                    "ver": "V1.2.1",
                    "brand": "gree",
                    "series": "gree",
                },
                generic,
                addr,
            )
            return
        if msg.get("i") == 1:
            inner = p.open_packet(msg, generic)
            assert inner["t"] == "bind"
            self.bound = True
            self._reply({"t": "bindok", "mac": MAC, "key": DEVICE_KEY, "r": 200}, generic, addr)
            return
        inner = p.open_packet(msg, DEVICE_KEY.encode())
        if inner["t"] == "status":
            cols = inner["cols"]
            self._reply(
                {"t": "dat", "mac": MAC, "r": 200, "cols": cols, "dat": [self.state[c] for c in cols]},
                DEVICE_KEY.encode(),
                addr,
            )
        elif inner["t"] == "cmd":
            self.commands.append((inner["opt"], inner["p"]))
            if self.obey:
                self.state.update(dict(zip(inner["opt"], inner["p"], strict=True)))
            self._reply(
                {"t": "res", "mac": MAC, "r": 200, "opt": inner["opt"], "p": inner["p"], "val": inner["p"]},
                DEVICE_KEY.encode(),
                addr,
            )


async def start_unit(**kw):
    loop = asyncio.get_running_loop()
    unit = FakeGreeUnit(**kw)
    transport, _ = await loop.create_datagram_endpoint(lambda: unit, local_addr=("127.0.0.1", 0))
    return unit, transport, transport.get_extra_info("sockname")[1]


class Sleeps:
    def __init__(self):
        self.calls = []

    async def __call__(self, s):
        self.calls.append(s)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_crypto_roundtrip(version):
    data = {"t": "status", "cols": ["Pow"], "mac": MAC}
    pkt = p.build_packet(data, DEVICE_KEY.encode(), version, tcid=MAC)
    assert ("tag" in pkt) == (version == "v2")
    assert p.open_packet(pkt, DEVICE_KEY.encode()) == data


def test_wrong_key_fails():
    pkt = p.build_packet({"t": "x"}, DEVICE_KEY.encode(), "v2")
    with pytest.raises(p.GreeProtocolError):
        p.open_packet(pkt, b"0" * 16)


@pytest.mark.parametrize("version", ["v1", "v2"])
async def test_pair_status_and_command_roundtrip(version):
    unit, transport, port = await start_unit(version=version)
    try:
        a = GreeAdapter({"port": port, "timeout_s": 1.0}, sleep=Sleeps())
        res = await a.pair("127.0.0.1")
        assert isinstance(res, PairResult)
        assert res.credentials == {"key": DEVICE_KEY}
        assert a.config["encryption"] == version and res.stable_identity == MAC
        assert res.device.extra["compatibility"] == "untested"

        events = await a.poll()
        st = next(e for e in events if isinstance(e, StateUpdate)).state
        assert st["mode"] == "heat" and st["target_c"] == 20 and st["power"] is False
        temp = next(e for e in events if isinstance(e, Reading))
        assert temp.value == 21  # TemSen 61 with +40 offset

        out = await a.execute(
            Command(MAC, "set_hvac", {"power": True, "mode": "heat", "target_c": 21.5, "fan": "medium"})
        )
        assert out.ok and out.confirmed
        assert out.state["target_c"] == 21.5
        opt, vals = unit.commands[-1]
        assert dict(zip(opt, vals, strict=True)) == {
            "Pow": 1,
            "Mod": 4,
            "TemUn": 0,
            "SetTem": 21,
            "TemRec": 1,
            "WdSpd": 3,
        }
    finally:
        transport.close()


async def test_readback_mismatch_not_confirmed():
    unit, transport, port = await start_unit(obey=False)
    try:
        a = GreeAdapter({"port": port, "timeout_s": 1.0}, sleep=Sleeps())
        await a.pair("127.0.0.1")
        out = await a.execute(Command(MAC, "set_hvac", {"target_c": 24}))
        assert out.ok is True and out.confirmed is False
        assert "readback" in out.detail
    finally:
        transport.close()


async def test_rate_limit_between_commands():
    unit, transport, port = await start_unit()
    try:
        sleeps = Sleeps()
        a = GreeAdapter({"port": port, "timeout_s": 1.0}, sleep=sleeps)
        await a.pair("127.0.0.1")
        await a.execute(Command(MAC, "set_hvac", {"target_c": 22}))
        assert sleeps.calls == []
        await a.execute(Command(MAC, "set_hvac", {"target_c": 23}))
        assert len(sleeps.calls) == 1 and 1.5 < sleeps.calls[0] <= 2.0
    finally:
        transport.close()


async def test_offline_unit_raises_after_bounded_retries():
    # Bind a socket to get a free port, then close it: nothing answers there.
    loop = asyncio.get_running_loop()
    t, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0))
    port = t.get_extra_info("sockname")[1]
    t.close()
    sleeps = Sleeps()
    a = GreeAdapter(
        {"host": "127.0.0.1", "port": port, "timeout_s": 0.2, "mac": MAC}, {"key": DEVICE_KEY}, sleep=sleeps
    )
    with pytest.raises(DeviceOffline):
        await a.read_status()
    assert len(sleeps.calls) == 2  # 3 attempts
    assert (await a.health()).state.value == "error"


async def test_not_paired():
    a = GreeAdapter({"host": "127.0.0.1"})
    with pytest.raises(PairingRequired):
        await a.read_status()
    assert (await a.health()).state.value == "not_configured"


def test_temperature_offset_and_plausibility():
    assert decode_temperature(61) == 21
    assert decode_temperature(21) == 21
    assert decode_temperature(0, offset=0) == 0
    assert decode_temperature(150) is None
    assert decode_temperature(None) is None


def test_encode_validation():
    from shc_integrations.base import AdapterError

    with pytest.raises(AdapterError):
        encode_command({"target_c": 40})
    with pytest.raises(AdapterError):
        encode_command({"mode": "turbo"})
    with pytest.raises(AdapterError):
        encode_command({})
    assert decode_status(["Mod"], [9])["mode"] == "unknown_9"
