"""Health round (every 60 s): adapter health per integration, sensor staleness, clock check.

- ``adapter.health()`` is called for every loaded integration (verbeterlijst 28). Its state and
  code feed the debounced integration status (``core.Worker.report``, source "health"); the full
  result (with the adapter's ``extra``: MQTT connection, dongle/bridge state, eventstream
  connected_since, Gree failures, HomeWizard serial check, ...) is stored in
  ``Integration.diagnostics["health"]`` for the system page and the integration detail.
- Hue eventstream down for more than 5 minutes: a "knoppen werken niet" warning (verbeterlijst 26).
- Clock: an SNTP query (UDP 123, short timeout, no extra dependency) against ``ntp_server`` every
  15 minutes; the result is the Setting "clock_check".
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .. import climate as cl
from ..db import sessionmaker
from ..models import ClimateZone, Device, Home, Integration
from ..store import raise_alert, resolve_alert, set_setting

log = logging.getLogger("shc.worker")

HEALTH_TIMEOUT_S = 20.0
EVENTSTREAM_ALERT_AFTER = timedelta(minutes=5)
CLOCK_EVERY_S = 15 * 60
NTP_TIMEOUT_S = 2.0
NTP_EPOCH = 2_208_988_800  # seconds between 1900-01-01 and 1970-01-01
CLOCK_WARN_S = 5.0
CLOCK_ERROR_S = 60.0


def _jsonable(value) -> dict:
    try:
        return json.loads(json.dumps(value or {}, default=str))
    except (TypeError, ValueError):
        return {}


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def split_host_port(server: str, default_port: int = 123) -> tuple[str, int]:
    s = server.strip()
    if s.startswith("["):  # [v6]:port
        host, _, rest = s[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") else default_port
    if s.count(":") == 1:
        host, port = s.split(":")
        return host, int(port)
    return s, default_port


def _ntp_time(raw: bytes) -> float:
    secs, frac = struct.unpack("!II", raw)
    return secs - NTP_EPOCH + frac / 2**32


def _ntp_bytes(t: float) -> bytes:
    secs = int(t) + NTP_EPOCH
    return struct.pack("!II", secs & 0xFFFFFFFF, int((t % 1) * 2**32) & 0xFFFFFFFF)


async def sntp_offset(server: str, wait_s: float = NTP_TIMEOUT_S) -> tuple[float, float]:
    """(clock offset in seconds, round-trip delay) of this machine against an NTP server.

    A positive offset means this machine runs behind. Raises OSError/TimeoutError/ValueError."""
    host, port = split_host_port(server)
    loop = asyncio.get_running_loop()
    answer: asyncio.Future = loop.create_future()

    class _Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr) -> None:
            if not answer.done():
                answer.set_result((data, time.time()))

        def error_received(self, exc: Exception) -> None:
            if not answer.done():
                answer.set_exception(exc)

    transport, _ = await asyncio.wait_for(
        loop.create_datagram_endpoint(_Proto, remote_addr=(host, port)), timeout=wait_s
    )
    try:
        t1 = time.time()
        packet = bytearray(48)
        packet[0] = 0x23  # LI 0, version 4, mode 3 (client)
        packet[40:48] = _ntp_bytes(t1)
        transport.sendto(bytes(packet))
        data, t4 = await asyncio.wait_for(answer, timeout=wait_s)
    finally:
        transport.close()
    if len(data) < 48 or data[0] & 0x07 not in (4, 5) or data[1] == 0:
        raise ValueError("Ongeldig of geweigerd NTP-antwoord")
    t2, t3 = _ntp_time(data[32:40]), _ntp_time(data[40:48])
    offset = ((t2 - t1) + (t3 - t4)) / 2
    delay = (t4 - t1) - (t3 - t2)
    return offset, delay


async def clock_check(server: str | None) -> dict:
    """The "clock_check" setting value: {state, detail, offset_s, server, checked_at}."""
    now = datetime.now(UTC).isoformat()
    if not server:
        return {
            "state": "unknown",
            "detail": "Tijdcontrole uitgeschakeld (geen NTP-server ingesteld).",
            "offset_s": None,
            "server": None,
            "checked_at": now,
        }
    try:
        offset, delay = await sntp_offset(server)
    except (OSError, TimeoutError, ValueError, struct.error) as exc:
        log.info("Tijdserver %s niet bereikbaar: %s", server, exc or type(exc).__name__)
        return {
            "state": "unknown",
            "detail": f"Tijdserver {server} niet bereikbaar; de klok is niet gecontroleerd.",
            "offset_s": None,
            "server": server,
            "checked_at": now,
        }
    a = abs(offset)
    state = "ok" if a < CLOCK_WARN_S else ("warn" if a < CLOCK_ERROR_S else "error")
    detail = f"Afwijking {offset:+.1f} s ten opzichte van {server}"
    if state != "ok":
        detail += ". Controleer de tijdsynchronisatie (NTP) van de NAS."
    return {
        "state": state,
        "detail": detail,
        "offset_s": round(offset, 3),
        "delay_s": round(delay, 3),
        "server": server,
        "checked_at": now,
    }


class HealthMixin:
    # Provided by core.Worker.
    adapters: dict[uuid.UUID, object]
    integrations: dict[uuid.UUID, Integration]
    _clock_checked: float = -1e9

    async def health_round(self) -> None:
        await self.integration_health_round()
        await self.sensor_round()
        await self.clock_round()

    # --- adapters ----------------------------------------------------------------------------

    async def integration_health_round(self) -> None:
        from shc_integrations.base import Health, HealthState

        from .core import error_code, error_text

        for iid, adapter in list(self.adapters.items()):
            integ = self.integrations.get(iid)
            if integ is None:
                continue
            try:
                h = await asyncio.wait_for(adapter.health(), timeout=HEALTH_TIMEOUT_S)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                h = Health(HealthState.ERROR, error_text(exc), code=error_code(exc))
            await self.record_health(integ, h)

    async def record_health(self, integ: Integration, h) -> None:
        from .core import patch_diagnostics

        now = datetime.now(UTC)
        state = str(getattr(h.state, "value", h.state))
        if state == "not_configured":
            state = "warn"
        extra = _jsonable(getattr(h, "extra", {}) or {})
        code = getattr(h, "code", None)
        await self.report(integ.id, "health", state, code, h.detail or "")
        st = self.status.get(integ.id)
        async with sessionmaker()() as db:
            await patch_diagnostics(
                db,
                integ.id,
                {
                    "health": {
                        "state": state,
                        "code": code,
                        "detail": h.detail or "",
                        "checked_at": now.isoformat(),
                        "extra": extra,
                    },
                    "polls": {
                        "missed": st.missed_polls if st else 0,
                        "consecutive_failures": st.consecutive_poll_failures if st else 0,
                    },
                },
            )
            key = f"eventstream:{integ.id}"
            since = _parse_iso(extra.get("disconnected_since"))
            if code == "eventstream_disconnected" and since and now - since > EVENTSTREAM_ALERT_AFTER:
                await raise_alert(
                    db,
                    integ.home_id,
                    key,
                    "warn",
                    "Hue-knoppen werken niet",
                    f"De verbinding voor knopgebeurtenissen met {integ.name} is verbroken sinds "
                    f"{since:%H:%M} UTC. Lampen bedienen werkt nog; knoppen die aan app-acties gekoppeld zijn "
                    "niet. De app probeert automatisch opnieuw te verbinden.",
                    "hue",
                )
            elif code != "eventstream_disconnected" or since is None:
                await resolve_alert(db, key)
            await db.commit()

    # --- sensors -------------------------------------------------------------------------------

    async def sensor_round(self) -> None:
        now = datetime.now(UTC)
        async with sessionmaker()() as db:
            zones = (
                (
                    await db.execute(
                        select(ClimateZone)
                        .join(Home, Home.id == ClimateZone.home_id)
                        .where(ClimateZone.sensor_device_id.is_not(None), Home.active_to.is_(None))
                    )
                )
                .scalars()
                .all()
            )
            for z in zones:
                pol = cl.policy_with_defaults(z.policy)
                dev = await db.get(Device, z.sensor_device_id)
                if dev is None:
                    continue
                key = f"stale:{dev.id}"
                if dev.last_seen is None or (now - dev.last_seen).total_seconds() > pol["stale_after_s"]:
                    mins = int((now - dev.last_seen).total_seconds() // 60) if dev.last_seen else None
                    await raise_alert(
                        db,
                        z.home_id,
                        key,
                        "warn",
                        f"Sensor {z.name} verouderd",
                        f"Laatste bericht {mins} min geleden" if mins is not None else "Nog geen bericht ontvangen",
                        "zigbee",
                    )
                else:
                    await resolve_alert(db, key)
            await db.commit()

    # --- clock ---------------------------------------------------------------------------------

    async def clock_round(self, force: bool = False) -> dict | None:
        mono = time.monotonic()
        if not force and mono - self._clock_checked < CLOCK_EVERY_S:
            return None
        self._clock_checked = mono
        result = await clock_check(self.settings.ntp_server)
        async with sessionmaker()() as db:
            await set_setting(db, "clock_check", result)
            await db.commit()
        return result
