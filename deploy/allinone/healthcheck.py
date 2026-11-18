"""De OCI-healthcheck wisselt van rechten voordat een dienst of bestand wordt gecontroleerd."""

import sys
import time
from pathlib import Path

from .runtime import ALIVE, API_READY, NGINX_HEALTH, drop_privileges, http_status, identity


def healthy(alive: Path = ALIVE) -> bool:
    if http_status(NGINX_HEALTH) != 200 or http_status(API_READY) != 200:
        return False
    try:
        return 0 <= time.time() - alive.stat().st_mtime < 180
    except OSError:
        return False


def main(args: list[str] | None = None) -> int:
    try:
        drop_privileges()
    except (OSError, ValueError, KeyError):
        print("Healthcheck geweigerd: rechtenwissel naar gebruiker 568 is mislukt.", file=sys.stderr)
        return 1
    if (sys.argv[1:] if args is None else args) == ["--toon-rechten"]:
        for name, value in identity().items():
            print(f"{name}: {value}")
        return 0
    return 0 if healthy() else 1


if __name__ == "__main__":
    raise SystemExit(main())
