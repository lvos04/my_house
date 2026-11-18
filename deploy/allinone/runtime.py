"""Identiteit en vaste lokale paden van de Custom App."""

import ctypes
import errno
import os
import urllib.error
import urllib.request
from pathlib import Path

UID = GID = 568
DATA = Path("/appdata")
PG_SOCKET = DATA / "postgres"
ALIVE = DATA / "app" / "worker-alive"
NGINX_HEALTH = "http://127.0.0.1:8080/healthz"
API_READY = "http://127.0.0.1:8000/readyz"
RECOVERY = "http://127.0.0.1:8000/api/v1/system/recovery"
IDENTITY = ("Uid", "Gid", "Groups", "CapEff", "CapPrm", "CapAmb", "CapBnd", "NoNewPrivs")


def prctl(option: int, value: int, extra: int = 0) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    call = libc.prctl
    call.argtypes = [ctypes.c_int, *[ctypes.c_ulong] * 4]
    if call(option, value, extra, 0, 0) == -1:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def identity() -> dict[str, str]:
    values = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        name, _, value = line.partition(":")
        if name in IDENTITY:
            values[name] = " ".join(value.split())
    return values


def drop_privileges(groups: tuple[int, ...] = ()) -> None:
    started_root = os.geteuid() == 0
    if 0 in groups:
        raise PermissionError(errno.EPERM, "Aanvullende groep 0 is geweigerd.")
    prctl(47, 4)  # PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL
    if started_root:
        last_cap = int(Path("/proc/sys/kernel/cap_last_cap").read_text())
        for capability in range(last_cap + 1):
            prctl(24, capability)  # PR_CAPBSET_DROP
        os.setgroups(groups)
        os.setgid(GID)
        os.setuid(UID)
    prctl(38, 1)  # PR_SET_NO_NEW_PRIVS

    actual = identity()
    expected_id = " ".join([str(UID)] * 4)
    allowed_groups = set(groups) if started_root else {GID}
    if (
        actual["Uid"] != expected_id
        or actual["Gid"] != expected_id
        or not {int(g) for g in actual["Groups"].split()} <= allowed_groups
        or any(int(actual[key], 16) for key in ("CapEff", "CapPrm", "CapAmb"))
        or (started_root and int(actual["CapBnd"], 16))
        or actual["NoNewPrivs"] != "1"
    ):
        raise PermissionError(errno.EPERM, "De procesrechten voldoen niet aan de Custom App-grens.")


def http_status(url: str) -> int:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=2) as response:
            return response.status
    except urllib.error.HTTPError as error:
        error.close()
        return error.code
    except (OSError, TimeoutError):
        return 0
