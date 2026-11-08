#!/usr/bin/env python3
"""Render infra/compose/compose.yaml into one YAML for TrueNAS SCALE (Apps › Install via YAML).

TrueNAS cannot pass --profile or an .env file, so optional services are enabled here and
all ${VAR:-default} references are resolved to concrete values. No secret ends up in the
output: the tunnel token stays in <root>/secrets/tunnel_token and is mounted as a file.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "compose" / "compose.yaml"
VERSION_FILE = HERE.parent.parent / "VERSION"
VAR = re.compile(r"\$\{([A-Z_]+)(?::-([^}]*))?\}")
PROFILES = {"zigbee", "tunnel"}
CLOUDFLARED_UID = 65532


def default_version() -> str | None:
    """The app version from the repository's VERSION file (single source, contract C9)."""
    try:
        v = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return v or None


def render(text: str, values: dict[str, str], enable: set[str]) -> str:
    out = []
    for line in text.splitlines():
        m = re.match(r'^\s+profiles: \["(\w+)"\]\s*$', line)
        if m and m.group(1) in enable:
            continue  # profile removed -> service always starts
        marker = re.search(r"#\s*only:(\w+)\s*$", line)
        if marker and marker.group(1) not in enable:
            continue  # e.g. the tunnel_token secret without the tunnel
        out.append(line)
    # Drop services whose profile was not enabled (TrueNAS would ignore them anyway). A block is
    # one two-space-indented key plus everything up to the next one; the comment lines right
    # above a key belong to that key's block.
    blocks: list[list[str]] = [[]]
    pending: list[str] = []
    for line in out:
        if re.match(r"^  #", line):
            pending.append(line)
            continue
        if re.match(r"^  [a-z0-9_]+:\s*$", line) or re.match(r"^\S", line):
            blocks.append(pending)
        else:
            blocks[-1].extend(pending)
        pending = []
        blocks[-1].append(line)
    blocks[-1].extend(pending)
    kept = [b for b in blocks if not any(re.match(r'^\s+profiles: \["\w+"\]', x) for x in b)]
    text = "\n".join(x for b in kept for x in b) + "\n"

    def sub(m: re.Match) -> str:
        name, default = m.group(1), m.group(2) or ""
        return values.get(name, default)

    text = VAR.sub(sub, text)
    if "${" in text:
        raise ValueError(
            "niet alle variabelen zijn ingevuld: " + ", ".join(sorted(set(re.findall(r"\$\{[^}]*\}", text))))
        )
    return text


def _net_prefix(value: str) -> str:
    parts = value.split(".")
    if len(parts) != 2 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        raise argparse.ArgumentTypeError("verwacht twee getallen, bijvoorbeeld 172.30")
    if not ipaddress.ip_address(f"{value}.250.2").is_private:
        raise argparse.ArgumentTypeError("gebruik een privé-adresbereik, bijvoorbeeld 172.30 of 10.250")
    return value


def _check_token_file(path: Path, ap: argparse.ArgumentParser) -> list[str]:
    """Problems with the tunnel token file that would stop cloudflared (or leak the token)."""
    if not path.is_file():
        ap.error(
            f"tunneltoken niet gevonden: zet het in {path} en voer install.sh opnieuw uit (zie docs/buiten-huis.md)"
        )
    warnings = []
    try:
        st = path.stat()
    except OSError as exc:  # pragma: no cover - permission problems on exotic setups
        return [f"kan {path} niet controleren: {exc}"]
    if st.st_size == 0:
        ap.error(f"{path} is leeg: plak het tunneltoken erin")
    if st.st_mode & 0o077:
        warnings.append(
            f"{path} is leesbaar voor anderen; voer install.sh opnieuw uit (zet 0400, eigenaar {CLOUDFLARED_UID})"
        )
    elif st.st_uid != CLOUDFLARED_UID and os.geteuid() == 0:
        warnings.append(f"{path} is niet van uid {CLOUDFLARED_UID} (cloudflared); voer install.sh opnieuw uit")
    return warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="dataset path, e.g. /mnt/tank/apps/smart-home")
    ap.add_argument("--with", dest="with_", default="", help="comma list: zigbee,tunnel")
    ap.add_argument("--port", default="8088")
    ap.add_argument("--version", default=default_version(), help="image version (default: the VERSION file)")
    ap.add_argument("--image-prefix", default="ghcr.io/lvos04/my_house")
    ap.add_argument("--lan-url", default="", help="address at home, e.g. http://192.168.1.20:8088")
    ap.add_argument("--remote-url", default="", help="tunnel address, e.g. https://huis.jouwdomein.nl")
    ap.add_argument("--zigbee-device", default="/dev/serial/by-id/CHANGE-ME")
    ap.add_argument(
        "--tunnel-token-file",
        help="only for older instructions: the token must be in <root>/secrets/tunnel_token",
    )
    ap.add_argument("--tz", default="Europe/Amsterdam")
    ap.add_argument(
        "--net-prefix",
        type=_net_prefix,
        default="172.30",
        help="first two parts of the internal networks (default 172.30); change only on a clash",
    )
    a = ap.parse_args(argv)
    if not a.version:
        ap.error("--version is verplicht (geen VERSION-bestand gevonden)")
    enable = {x.strip() for x in a.with_.split(",") if x.strip()}
    unknown = enable - PROFILES
    if unknown:
        ap.error(f"onbekend onderdeel: {', '.join(sorted(unknown))}")
    if not a.port.isdigit() or not 1 <= int(a.port) <= 65535:
        ap.error("--port moet een poortnummer zijn")
    if "zigbee" in enable and "CHANGE-ME" in a.zigbee_device:
        ap.error("--zigbee-device is verplicht met zigbee (zie: ls -l /dev/serial/by-id/)")
    if a.lan_url and not re.match(r"^https?://", a.lan_url):
        ap.error("--lan-url moet met http:// of https:// beginnen")
    if a.remote_url and not a.remote_url.startswith("https://"):
        ap.error("--remote-url moet met https:// beginnen")
    root = a.root.rstrip("/") or "/"
    warnings: list[str] = []
    if "tunnel" in enable:
        token = Path(root) / "secrets" / "tunnel_token"
        if a.tunnel_token_file and Path(a.tunnel_token_file).resolve() != token.resolve():
            ap.error(
                f"het tunneltoken staat niet meer in de YAML maar wordt als bestand gelezen: "
                f"verplaats {a.tunnel_token_file} naar {token} en voer install.sh opnieuw uit"
            )
        warnings += _check_token_file(token, ap)
        if not a.remote_url:
            warnings.append(
                "--with tunnel zonder --remote-url: telefoons krijgen bij het koppelen geen adres voor onderweg"
            )
    values = {
        "SHC_ROOT": root,
        "SHC_HTTP_PORT": a.port,
        "SHC_VERSION": a.version,
        "SHC_IMAGE_PREFIX": a.image_prefix,
        "SHC_LAN_URL": a.lan_url,
        "SHC_REMOTE_URL": a.remote_url,
        "SHC_ZIGBEE_DEVICE": a.zigbee_device,
        "SHC_NET_PREFIX": a.net_prefix,
        "TZ": a.tz,
    }
    try:
        text = render(SRC.read_text(encoding="utf-8"), values, enable)
    except ValueError as exc:
        ap.error(str(exc))
    for w in warnings:
        print(f"LET OP: {w}", file=sys.stderr)
    header = (
        f"# Smart Home Control {a.version}, gemaakt met render_truenas.py"
        f" (onderdelen: {', '.join(sorted(enable)) or 'basis'}). Bevat geen geheimen.\n"
    )
    sys.stdout.write(header + text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
