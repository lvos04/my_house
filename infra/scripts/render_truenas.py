#!/usr/bin/env python3
"""Render infra/compose/compose.yaml into one YAML for TrueNAS SCALE (Apps › Install via YAML).

TrueNAS cannot pass --profile or an .env file, so optional services are enabled here and
all ${VAR:-default} references are resolved to concrete values.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "compose" / "compose.yaml"
VAR = re.compile(r"\$\{([A-Z_]+)(?::-([^}]*))?\}")


def render(text: str, values: dict[str, str], enable: set[str]) -> str:
    out = []
    for line in text.splitlines():
        m = re.match(r'^\s+profiles: \["(\w+)"\]\s*$', line)
        if m:
            if m.group(1) in enable:
                continue  # profile removed -> service always starts
            out.append(line)
            continue
        out.append(line)
    text = "\n".join(out) + "\n"
    # drop services whose profile was not enabled (TrueNAS would ignore them anyway)
    blocks = re.split(r"(?m)^(?=  [a-z0-9_]+:\n)", text)
    kept = [b for b in blocks if not re.search(r'^\s+profiles: \["\w+"\]', b, re.M)]
    text = "".join(kept)

    def sub(m: re.Match) -> str:
        name, default = m.group(1), m.group(2) or ""
        return values.get(name, default)

    return VAR.sub(sub, text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, help="dataset path, e.g. /mnt/tank/apps/smart-home")
    ap.add_argument("--with", dest="with_", default="", help="comma list: zigbee,tunnel")
    ap.add_argument("--port", default="8088")
    ap.add_argument("--version", default="0.1.0")
    ap.add_argument("--image-prefix", default="ghcr.io/lvos04/my_house")
    ap.add_argument("--lan-url", default="")
    ap.add_argument("--remote-url", default="")
    ap.add_argument("--zigbee-device", default="/dev/serial/by-id/CHANGE-ME")
    ap.add_argument("--tunnel-token-file", help="file with the Cloudflare tunnel token")
    ap.add_argument("--tz", default="Europe/Amsterdam")
    a = ap.parse_args(argv)
    enable = {x for x in a.with_.split(",") if x}
    unknown = enable - {"zigbee", "tunnel"}
    if unknown:
        ap.error(f"onbekend onderdeel: {', '.join(unknown)}")
    if "zigbee" in enable and "CHANGE-ME" in a.zigbee_device:
        ap.error("--zigbee-device is verplicht met zigbee (zie: ls -l /dev/serial/by-id/)")
    token = ""
    if "tunnel" in enable:
        if not a.tunnel_token_file:
            ap.error("--tunnel-token-file is verplicht met tunnel")
        token = Path(a.tunnel_token_file).read_text().strip()
    values = {
        "SHC_ROOT": a.root.rstrip("/"), "SHC_HTTP_PORT": a.port, "SHC_VERSION": a.version,
        "SHC_IMAGE_PREFIX": a.image_prefix, "SHC_LAN_URL": a.lan_url, "SHC_REMOTE_URL": a.remote_url,
        "SHC_ZIGBEE_DEVICE": a.zigbee_device, "SHC_TUNNEL_TOKEN": token, "TZ": a.tz,
        "SHC_COOKIE_SECURE": "true" if a.remote_url.startswith("https://") else "false",
    }
    sys.stdout.write(render(SRC.read_text(), values, enable))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
