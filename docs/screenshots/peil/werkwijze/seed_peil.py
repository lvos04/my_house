"""Testgegevens voor de Peil-schermafdrukken (buiten de repo; niet committen).

Draai met de Python van de API en dezelfde SHC_*-omgeving als de API (run_shots.sh zet die).
Schrijft rechtstreeks in de wegwerp-testdatabase, zoals apps/web/e2e/seed.py: apparaten zoals de
worker ze zou hebben gevonden (geen echte hardware), metingen, rolkoppelingen en meldingen.
Alle namen en waarden zijn fictief. Nooit tegen een echte installatie gebruiken.

    python seed_peil.py all '{"home_id": "..."}'
    python seed_peil.py stale '{"home_id": "..."}'   # maakt sensorwaarden en vermogen verouderd
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

GREE_CAPS = {
    "modes": ["off", "auto", "cool", "dry", "fan", "heat"],
    "min_c": 16,
    "max_c": 30,
    "step_c": 1,
    "fan": True,
    "swing": True,
}


def _dev(Device, home_id, now, **kw):
    online = kw.pop("online", True)
    return Device(
        home_id=home_id,
        provider=kw.pop("provider"),
        kind=kw.pop("kind"),
        name=kw.pop("name"),
        stable_identity=kw.pop("stable_identity", None) or f"peil-{uuid.uuid4().hex[:12]}",
        model=kw.pop("model", None),
        firmware=kw.pop("firmware", None),
        capabilities=kw.pop("capabilities", []),
        compatibility=kw.pop("compatibility", "untested"),
        lifecycle="active",
        online=online,
        last_seen=now if online else now - timedelta(hours=3),
        state=kw.pop("state", {}),
        discovered_at=now - timedelta(days=12),
        calibration={},
        diagnostics={},
    )


async def all_(a: dict) -> dict:
    from shc_api import store
    from shc_api.db import sessionmaker
    from shc_api.models import Device, DeviceBinding, LogicalRole, SensorReading, Tariff
    from sqlalchemy import select

    home_id = uuid.UUID(a["home_id"])
    now = datetime.now(UTC)
    tz = ZoneInfo("Europe/Amsterdam")
    out: dict = {}
    async with sessionmaker()() as db:
        # Airco-units (gedeelde buitenunit): woonkamer verwarmt, slaapkamer staat uit.
        ac1 = _dev(
            Device,
            home_id,
            now,
            provider="gree",
            kind="ac_unit",
            name="Airco woonkamer",
            model="Gree (fictief)",
            capabilities=["mode", "target_c", "fan", "swing"],
            state={"power": True, "mode": "heat", "target_c": 21, "fan": "auto", "indoor_c": 20.5, "caps": GREE_CAPS},
        )
        ac2 = _dev(
            Device,
            home_id,
            now,
            provider="gree",
            kind="ac_unit",
            name="Airco slaapkamer",
            model="Gree (fictief)",
            capabilities=["mode", "target_c", "fan", "swing"],
            state={"power": False, "mode": "off", "target_c": 18, "fan": "auto", "indoor_c": 18.5, "caps": GREE_CAPS},
        )
        # Zigbee-sensoren
        s1 = _dev(
            Device,
            home_id,
            now,
            provider="zigbee",
            kind="zigbee_sensor",
            name="Sensor woonkamer",
            model="Aqara (fictief)",
            capabilities=["temperature", "humidity", "battery"],
            state={"battery": 87, "linkquality": 120},
        )
        s2 = _dev(
            Device,
            home_id,
            now,
            provider="zigbee",
            kind="zigbee_sensor",
            name="Sensor slaapkamer",
            model="SONOFF (fictief)",
            capabilities=["temperature", "humidity", "battery"],
            state={"battery": 64, "linkquality": 88},
        )
        # Hue: drie groepen (één onbereikbaar), scènes en losse lampen
        g1 = _dev(
            Device,
            home_id,
            now,
            provider="hue",
            kind="light_group",
            name="Woonkamer",
            state={
                "on": True,
                "brightness": 80,
                "lights_on": 3,
                "lights_total": 3,
                "lights_reachable": 3,
                "owner_type": "room",
            },
        )
        g2 = _dev(
            Device,
            home_id,
            now,
            provider="hue",
            kind="light_group",
            name="Keuken",
            state={
                "on": False,
                "brightness": 0,
                "lights_on": 0,
                "lights_total": 2,
                "lights_reachable": 2,
                "owner_type": "room",
            },
        )
        g3 = _dev(
            Device,
            home_id,
            now,
            provider="hue",
            kind="light_group",
            name="Tuin",
            online=False,
            state={
                "on": False,
                "brightness": 0,
                "lights_on": 0,
                "lights_total": 1,
                "lights_reachable": 0,
                "owner_type": "zone",
            },
        )
        db.add_all([ac1, ac2, s1, s2, g1, g2, g3])
        await db.flush()
        g1.state = {**g1.state, "scene_id": "peil-scene-lezen", "scene": "Lezen"}
        db.add_all(
            [
                _dev(
                    Device,
                    home_id,
                    now,
                    provider="hue",
                    kind="scene",
                    name="Lezen",
                    stable_identity="peil-scene-lezen",
                    state={"group_device_id": str(g1.id)},
                ),
                _dev(
                    Device,
                    home_id,
                    now,
                    provider="hue",
                    kind="scene",
                    name="Ontspannen",
                    stable_identity="peil-scene-ontspannen",
                    state={"group_device_id": str(g1.id)},
                ),
                _dev(
                    Device,
                    home_id,
                    now,
                    provider="hue",
                    kind="light",
                    name="Staande lamp",
                    model="Hue (fictief)",
                    state={"on": True, "brightness": 80, "group_device_id": str(g1.id)},
                ),
                _dev(
                    Device,
                    home_id,
                    now,
                    provider="hue",
                    kind="light",
                    name="Plafondlamp",
                    model="Hue (fictief)",
                    state={"on": True, "brightness": 80, "group_device_id": str(g1.id)},
                ),
                _dev(
                    Device,
                    home_id,
                    now,
                    provider="hue",
                    kind="light",
                    name="Tuinspot",
                    model="Hue (fictief)",
                    online=False,
                    state={"on": False, "reachable": False, "group_device_id": str(g3.id)},
                ),
            ]
        )
        # HomeWizard: P1 (huis totaal), 1-fase kWh-meter (airco), Energy Socket (boiler, alleen meten)
        p1 = _dev(
            Device,
            home_id,
            now,
            provider="homewizard",
            kind="p1_meter",
            name="P1-meter",
            model="HWE-P1 (fictief)",
            capabilities=["power", "energy"],
            state={},
        )
        kwh = _dev(
            Device,
            home_id,
            now,
            provider="homewizard",
            kind="kwh_meter",
            name="kWh-meter airco",
            model="HWE-KWH1 (fictief)",
            capabilities=["power", "energy"],
            state={},
        )
        sock = _dev(
            Device,
            home_id,
            now,
            provider="homewizard",
            kind="energy_socket",
            name="Stekker boiler",
            model="HWE-SKT (fictief)",
            capabilities=["power", "energy", "relay"],
            state={"switch_on": True},
        )
        db.add_all([p1, kwh, sock])
        await db.flush()

        roles = {
            r.key: r for r in (await db.execute(select(LogicalRole).where(LogicalRole.home_id == home_id))).scalars()
        }
        for key, dev in (("grid", p1), ("airco", kwh), ("boiler", sock)):
            if key in roles:
                db.add(DeviceBinding(role_id=roles[key].id, device_id=dev.id, effective_from=now - timedelta(days=12)))

        # Sensorwaarden (2 min oud = vers)
        def reading(dev, metric, value, unit, at, quality="measured"):
            return SensorReading(
                device_id=dev.id,
                metric=metric,
                value=value,
                unit=unit,
                quality=quality,
                observed_at=at,
                received_at=at,
                event_id=f"peil-{metric}-{uuid.uuid4().hex[:10]}",
            )

        at = now - timedelta(minutes=2)
        db.add_all(
            [
                reading(s1, "temperature", 20.6, "°C", at),
                reading(s1, "humidity", 48.0, "%", at),
                reading(s1, "battery", 87, "%", at),
                reading(s2, "temperature", 18.4, "°C", at),
                reading(s2, "humidity", 55.0, "%", at),
                reading(s2, "battery", 64, "%", at),
            ]
        )
        # Temperatuurverloop laatste 24 uur (voor "Laatste 24 uur")
        for i in range(1, 96):
            t = now - timedelta(minutes=15 * i)
            ph = math.sin(2 * math.pi * (t.astimezone(tz).hour * 60 + t.astimezone(tz).minute - 6 * 60) / 1440)
            db.add(reading(s1, "temperature", round(19.8 + 0.9 * ph, 1), "°C", t))
            db.add(reading(s2, "temperature", round(18.0 + 0.5 * ph, 1), "°C", t))

        # Energie: tellerstanden elke 15 min vanaf gisteren 00:00 lokaal tot nu
        start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        start = start.astimezone(UTC)
        counters = {p1.id: 12_345.0, kwh.id: 2_110.0, sock.id: 4_020.0}
        t = start
        k = 0
        while t <= now:
            h = t.astimezone(tz).hour
            grid_kw = 0.35 + (0.9 if 17 <= h <= 21 else 0.0) + (0.25 if 7 <= h <= 9 else 0.0)
            airco_kw = 0.55 if (6 <= h <= 9 or 17 <= h <= 22) else 0.08
            boiler_kw = 2.0 if h in (3, 4) else 0.0
            for dev, kw_ in ((kwh, airco_kw), (sock, boiler_kw)):
                counters[dev.id] += kw_ / 4
            counters[p1.id] += (grid_kw + airco_kw + boiler_kw) / 4
            for dev in (p1, kwh, sock):
                db.add(reading(dev, "energy_import_kwh", round(counters[dev.id], 3), "kWh", t))
            k += 1
            t += timedelta(minutes=15)
        pw_at = now - timedelta(seconds=20)
        db.add_all(
            [
                reading(p1, "power_w", 1840.0, "W", pw_at),
                reading(kwh, "power_w", 610.0, "W", pw_at),
                reading(sock, "power_w", 0.0, "W", pw_at),
            ]
        )

        db.add(
            Tariff(
                home_id=home_id,
                valid_from=(now - timedelta(days=60)).date(),
                import_per_kwh=0.27,
                export_per_kwh=0.08,
                fixed_per_day=0.95,
                source="handmatig",
            )
        )
        await db.commit()

        await store.raise_alert(
            db,
            home_id,
            "peil:sensor-batterij",
            "warn",
            "Sensor slaapkamer: batterij 64 %",
            "Vervang de batterij binnen een paar weken.",
            "zigbee",
        )
        await store.raise_alert(
            db,
            home_id,
            "peil:backup",
            "info",
            "Nog geen automatische back-up ingesteld",
            "Stel back-ups in bij Systeem › Back-ups en bewaren.",
            "system",
        )
        await db.commit()
        out = {"ac1": str(ac1.id), "ac2": str(ac2.id), "s1": str(s1.id), "s2": str(s2.id), "readings": k}
    return out


async def stale(a: dict) -> dict:
    """Maakt de laatste sensorwaarden 3 uur oud (verouderd) — voor de verouderd-toestand."""
    from shc_api.db import sessionmaker
    from sqlalchemy import text

    async with sessionmaker()() as db:
        await db.execute(text("UPDATE sensor_readings SET observed_at = observed_at - interval '3 hours'"))
        await db.commit()
    return {"ok": True}


async def heartbeat_loop(a: dict) -> dict:
    """Zet elke 20 s de worker-hartslag (er draait geen worker in deze testopstelling)."""
    from shc_api.db import sessionmaker
    from shc_api.store import set_setting

    while True:
        async with sessionmaker()() as db:
            await set_setting(db, "worker_heartbeat", datetime.now(UTC).isoformat())
            await db.commit()
        await asyncio.sleep(20)


COMMANDS = {"all": all_, "stale": stale, "heartbeat_loop": heartbeat_loop}


async def main(cmd: str, raw: str) -> dict:
    from shc_api.db import dispose

    try:
        return await COMMANDS[cmd](json.loads(raw or "{}"))
    finally:
        await dispose()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "{}"))))
