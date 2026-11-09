from __future__ import annotations

import asyncio
import socket
import struct
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from .. import views
from ..config import get_settings
from ..deps import DB, Admin, Auth, Control, get_home
from ..errors import not_found
from ..models import Alert, ClimateZone, Home, Integration, LogicalRole, Room
from ..store import audit, get_setting
from ..views.status import integration_details, sync_worker_alert, worker_check, zigbee_parts

router = APIRouter(tags=["homes"])

DEFAULT_ROLES = [
    ("grid", "Huis totaal (P1)", False),
    ("airco", "Airco totaal (1-fase kWh-meter)", False),
    ("boiler", "Boiler (alleen meten)", True),
    ("living_room_climate", "Klimaat woonkamer", False),
    ("bedroom_climate", "Klimaat slaapkamer", False),
    ("outdoor_temp", "Buitentemperatuur (optioneel)", False),
]


class HomeIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    timezone: str = "Europe/Amsterdam"
    currency: str = Field(default="EUR", min_length=3, max_length=3)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        if v not in available_timezones():
            raise ValueError("Onbekende tijdzone")
        return v


@router.get("/homes")
async def homes(db: DB, p: Auth) -> list[dict]:
    rows = (await db.execute(select(Home).order_by(Home.active_from))).scalars()
    return [views.home_view(h) for h in rows]


@router.post("/homes")
async def create_home(body: HomeIn, db: DB, p: Admin) -> dict:
    h = Home(name=body.name, timezone=body.timezone, currency=body.currency)
    db.add(h)
    await db.flush()
    for key, label, protected in DEFAULT_ROLES:
        db.add(LogicalRole(home_id=h.id, key=key, label=label, protected=protected))
    lr = Room(home_id=h.id, name="Woonkamer", function="living")
    br = Room(home_id=h.id, name="Slaapkamer", function="bedroom")
    db.add_all([lr, br])
    await db.flush()
    db.add_all(
        [
            ClimateZone(home_id=h.id, name="Woonkamer", room_id=lr.id, outdoor_group="multisplit-1"),
            ClimateZone(home_id=h.id, name="Slaapkamer", room_id=br.id, outdoor_group="multisplit-1"),
        ]
    )
    await audit(db, p.actor, "home_created", body.name)
    await db.commit()
    return views.home_view(h)


@router.get("/homes/{home_id}/overview")
async def overview(home_id: uuid.UUID, db: DB, p: Auth) -> dict:
    home = await get_home(db, home_id)
    if home.active_to is None:
        # The worker cannot report its own absence: raise/resolve that alert here (verbeterlijst 22).
        await sync_worker_alert(db, home_id)
        await db.commit()
    zones = (
        (await db.execute(select(ClimateZone).where(ClimateZone.home_id == home_id).order_by(ClimateZone.name.desc())))
        .scalars()
        .all()
    )
    tz = ZoneInfo(home.timezone)
    today = datetime.now(tz).date()
    return {
        "home": views.home_view(home),
        "status": await views.status_checks(db, home_id),
        "zones": [await views.zone_view(db, z, home) for z in zones],
        "lights": (await views.lights_view(db, home_id))["groups"],
        "energy_today": await views.energy_summary(db, home, "day", today),
        "power_now": await views.live_power(db, home_id),
        "alerts": await views.open_alerts(db, home_id),
    }


@router.get("/homes/{home_id}/alerts")
async def alerts(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    await get_home(db, home_id)
    return await views.open_alerts(db, home_id, 100)


@router.post("/alerts/{alert_id}/ack")
async def ack(alert_id: uuid.UUID, db: DB, p: Control) -> dict:
    a = await db.get(Alert, alert_id)
    if not a:
        raise not_found("Melding")
    a.acknowledged_at = datetime.now(UTC)
    await audit(db, p.actor, "alert_acknowledged", a.title, alert=str(a.id), key=a.key)
    await db.commit()
    from ..store import alert_view

    return alert_view(a)


@router.get("/homes/{home_id}/rooms")
async def rooms(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    rows = (await db.execute(select(Room).where(Room.home_id == home_id))).scalars()
    return [{"id": r.id, "name": r.name, "function": r.function} for r in rows]


# --- installation preflight (verbeterlijst 43, plan §7) ----------------------------------------------

MDNS_ADDR = ("224.0.0.251", 5353)
MDNS_WAIT_S = 1.5


def _mdns_query() -> bytes:
    """A one-shot (legacy unicast) mDNS query for all advertised service types."""
    header = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in "_services._dns-sd._udp.local".split(".")) + b"\0"
    return header + qname + struct.pack("!HH", 12, 1)  # PTR, IN


async def discovery_probe(wait_s: float = MDNS_WAIT_S) -> tuple[str, str]:
    """Can this container find devices on the home network? (state, Dutch detail)."""
    loop = asyncio.get_running_loop()
    answered: asyncio.Future = loop.create_future()

    class _Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr) -> None:
            if not answered.done():
                answered.set_result(addr)

        def error_received(self, exc: Exception) -> None:
            if not answered.done():
                answered.set_exception(exc)

    unavailable = (
        "warn",
        "Automatisch zoeken is niet beschikbaar in het containernetwerk; vul de IP-adressen van de apparaten "
        "handmatig in (en geef ze een vast adres in je router).",
    )
    try:
        transport, _ = await loop.create_datagram_endpoint(
            _Proto,
            local_addr=("0.0.0.0", 0),  # noqa: S104 - an ephemeral client port, not a listening service
            family=socket.AF_INET,
        )
    except OSError:
        return unavailable
    try:
        sock = transport.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        transport.sendto(_mdns_query(), MDNS_ADDR)
        addr = await asyncio.wait_for(answered, timeout=wait_s)
    except (TimeoutError, OSError):
        return unavailable
    finally:
        transport.close()
    return "ok", f"Netwerkdetectie werkt (antwoord van {addr[0]})."


async def _zigbee_checks(db: DB) -> list[dict]:
    integ = (
        (
            await db.execute(
                select(Integration)
                .join(Home, Home.id == Integration.home_id)
                .where(Integration.provider == "zigbee", Integration.enabled.is_(True), Home.active_to.is_(None))
            )
        )
        .scalars()
        .first()
    )
    if integ is None:
        return [
            {
                "key": "zigbee",
                "label": "Zigbee",
                "state": "unknown",
                "detail": "Zigbee is nog niet ingesteld (optioneel; nodig voor eigen sensoren).",
            }
        ]
    parts = zigbee_parts(integ, integration_details(integ)["extra"])
    for part in parts:
        part["key"] = "mqtt" if part["key"].endswith(":mqtt") else "zigbee_dongle"
    return parts


@router.get("/system/preflight")
async def preflight(db: DB, p: Admin) -> dict:
    """Checks before/while installing: home, worker, Zigbee (MQTT + USB dongle), network discovery,
    clock, home address and backups. Reads only."""
    s = get_settings()
    now = datetime.now(UTC)
    checks: list[dict] = []
    home = (await db.execute(select(Home).where(Home.active_to.is_(None)).limit(1))).scalars().first()
    checks.append(
        {
            "key": "home",
            "label": "Woning",
            "state": "ok" if home else "error",
            "detail": f"Woning ‘{home.name}’" if home else "Maak eerst een woning aan (naam en tijdzone).",
        }
    )
    checks.append(await worker_check(db, now))
    checks += await _zigbee_checks(db)
    state, detail = await discovery_probe()
    checks.append({"key": "discovery", "label": "Netwerkdetectie", "state": state, "detail": detail})
    clock = await get_setting(db, "clock_check")
    if isinstance(clock, dict):
        checks.append(
            {
                "key": "clock",
                "label": "Klok",
                "state": clock.get("state", "unknown"),
                "detail": clock.get("detail", ""),
            }
        )
    else:
        checks.append(
            {
                "key": "clock",
                "label": "Klok",
                "state": "unknown",
                "detail": "Nog niet gecontroleerd; de achtergronddienst vergelijkt de klok elke 15 minuten met een "
                "tijdserver.",
            }
        )
    lan = await get_setting(db, "lan_url", s.lan_url)
    checks.append(
        {
            "key": "lan_url",
            "label": "Thuisadres",
            "state": "ok" if lan else "warn",
            "detail": f"Telefoons verbinden thuis via {lan}"
            if lan
            else "Stel het thuisadres in op de pagina Systeem (nodig om telefoons te koppelen).",
        }
    )
    from ..worker.backup_job import load_backup_settings

    try:
        cfg = await load_backup_settings(db, s)
    except Exception:
        cfg = {"enabled": False, "passphrase_set": False}
    if cfg.get("enabled"):
        b_state, b_detail = "ok", f"Automatische back-up dagelijks om {cfg['hour']:02d}:00, {cfg['keep']} bewaard."
    elif cfg.get("passphrase_error"):
        b_state, b_detail = "error", cfg["passphrase_error"]
    elif cfg.get("passphrase_set"):
        b_state, b_detail = "warn", "Automatische back-ups staan uit."
    else:
        b_state, b_detail = "warn", "Stel een wachtwoordzin voor back-ups in op de pagina Systeem."
    checks.append({"key": "backup", "label": "Back-up", "state": b_state, "detail": b_detail})
    return {"checked_at": now, "ready": not any(c["state"] == "error" for c in checks), "checks": checks}
