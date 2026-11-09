"""A fake Gree indoor unit speaking the LAN protocol over real UDP (not a real device).

`version` is the encryption the unit really uses. `scan_version` lets a unit answer the scan in
another format than it binds with (newer modules answer the scan in v1 but only accept a v2 bind).
`bind_versions` are the encryptions the unit answers a bind for; other binds are ignored (no reply),
exactly what makes the pairing time out on such modules.
"""

from __future__ import annotations

import asyncio
import json

from shc_integrations.gree import protocol as p

DEVICE_KEY = "St8Vw1Yz4Bc7Ef0H"
MAC = "f4911e000001"


class FakeGreeUnit(asyncio.DatagramProtocol):
    def __init__(self, version="v1", obey=True, scan_version=None, bind_versions=None, state=None, scan_info=None):
        self.version = version
        self.scan_version = scan_version or version
        self.bind_versions = set(bind_versions) if bind_versions is not None else {version}
        self.obey = obey
        self.bound = False
        self.bind_requests: list[str] = []
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
        if state:
            self.state.update(state)
        self.scan_info = scan_info or {
            "t": "dev",
            "cid": MAC,
            "mac": MAC,
            "mid": "10001",
            "model": "gree",
            "name": "unit1",
            "ver": "V1.2.1",
            "brand": "gree",
            "series": "gree",
        }
        self.commands = []

    def connection_made(self, transport):
        self.transport = transport

    def _reply(self, inner, key, addr, version=None):
        out = {"t": "pack", "i": 0, "uid": 0, "cid": MAC, "tcid": ""}
        if (version or self.version) == "v2":
            out["pack"], out["tag"] = p.encrypt_v2(inner, key)
        else:
            out["pack"] = p.encrypt_v1(inner, key)
        self.transport.sendto(json.dumps(out).encode(), addr)

    def datagram_received(self, data, addr):
        msg = json.loads(data)
        if msg.get("t") == "scan":
            generic = p.GENERIC_KEY_V2 if self.scan_version == "v2" else p.GENERIC_KEY_V1
            self._reply(self.scan_info, generic, addr, self.scan_version)
            return
        if msg.get("i") == 1:
            used = "v2" if "tag" in msg else "v1"
            self.bind_requests.append(used)
            if used not in self.bind_versions:
                return  # unit ignores a bind in the wrong encryption
            generic = p.GENERIC_KEY_V2 if used == "v2" else p.GENERIC_KEY_V1
            inner = p.open_packet(msg, generic)
            assert inner["t"] == "bind"
            self.bound = True
            self.version = used
            self._reply({"t": "bindok", "mac": MAC, "key": DEVICE_KEY, "r": 200}, generic, addr, used)
            return
        inner = p.open_packet(msg, DEVICE_KEY.encode())
        if inner["t"] == "status":
            cols = inner["cols"]
            self._reply(
                {"t": "dat", "mac": MAC, "r": 200, "cols": cols, "dat": [self.state.get(c, 0) for c in cols]},
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


async def closed_udp_port() -> int:
    loop = asyncio.get_running_loop()
    t, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0))
    port = t.get_extra_info("sockname")[1]
    t.close()
    return port


class Sleeps:
    def __init__(self):
        self.calls = []

    async def __call__(self, s):
        self.calls.append(s)
