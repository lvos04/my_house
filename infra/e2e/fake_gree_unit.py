"""A fake Gree airco unit on the "home network" of the browser tests against the compose stack.

Runs in the API image (it needs shc_integrations.gree.protocol) next to the stack and speaks the real
LAN protocol over UDP port 7000: scan, bind, status and cmd, like integrations/tests/fake_gree.py,
whose FakeGreeUnit it serves. The API pairs it like a real unit, the worker polls it and its commands
are applied and read back, so a command from the browser is confirmed by the worker exactly as at home.

Not a real device and never part of an installation: only infra/e2e/compose.e2e.yaml starts it.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_gree import FakeGreeUnit  # noqa: E402 - integrations/tests/fake_gree.py, mounted next to this file

PORT = int(os.environ.get("FAKE_GREE_PORT", "7000"))


class LoggingUnit(FakeGreeUnit):
    def datagram_received(self, data, addr):
        try:
            kind = json.loads(data).get("t")
        except ValueError:
            kind = "?"
        before = len(self.commands)
        super().datagram_received(data, addr)
        if len(self.commands) > before:
            opt, values = self.commands[-1]
            print(f"cmd from {addr[0]}: {dict(zip(opt, values, strict=True))}", flush=True)
        elif kind != "pack":
            print(f"{kind} from {addr[0]}", flush=True)


async def main() -> None:
    loop = asyncio.get_running_loop()
    # Heating at 21 °C, 20 °C indoors (TemSen is the indoor temperature + 40).
    unit = LoggingUnit(version="v1", state={"Pow": 1, "Mod": 4, "SetTem": 21, "TemSen": 60})
    await loop.create_datagram_endpoint(lambda: unit, local_addr=("0.0.0.0", PORT))  # noqa: S104
    print(f"fake Gree unit listening on udp/{PORT}", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
