"""Test data for the browser tests that no API endpoint creates: devices as the worker would have
found them (no real hardware), Hue button mappings as stored by an older version, alerts, and
readings (a measured series) as the worker would have stored them.

Run with the API's Python and the same SHC_* environment as the API under test (e2e/run-stack.sh
exports it). Usage:

    python e2e/seed.py <command> '<json>'

Prints one JSON object. Alerts go through shc_api.store, so they are published over PostgreSQL
NOTIFY exactly like the worker's (alert.created / alert.resolved reach the browser over the API's
WebSocket). Never point this at a real installation: it writes straight into the database.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta


def _uuid(v: str | None) -> uuid.UUID | None:
    return uuid.UUID(v) if v else None


async def device(a: dict) -> dict:
    from shc_api.db import sessionmaker
    from shc_api.models import Device

    now = datetime.now(UTC)
    async with sessionmaker()() as db:
        d = Device(
            home_id=uuid.UUID(a["home_id"]),
            provider=a.get("provider", "gree"),
            kind=a["kind"],
            name=a["name"],
            stable_identity=a.get("stable_identity") or f"e2e-{uuid.uuid4().hex[:12]}",
            model=a.get("model"),
            firmware=a.get("firmware"),
            capabilities=a.get("capabilities", []),
            compatibility=a.get("compatibility", "untested"),
            lifecycle="active",
            online=bool(a.get("online", True)),
            last_seen=now if a.get("online", True) else None,
            state=a.get("state", {}),
            discovered_at=now,
            calibration={},
            diagnostics={},
        )
        db.add(d)
        await db.commit()
        return {"id": str(d.id), "name": d.name}


async def mapping(a: dict) -> dict:
    """A button mapping stored as-is, without the API's conflict check (data from an older version)."""
    from shc_api.db import sessionmaker
    from shc_api.models import ButtonMapping

    async with sessionmaker()() as db:
        m = ButtonMapping(
            home_id=uuid.UUID(a["home_id"]),
            device_id=uuid.UUID(a["device_id"]),
            button=a["button"],
            event=a["event"],
            mode=a["mode"],
            action=a["action"],
            combined_confirmed=bool(a.get("combined_confirmed", False)),
            enabled=bool(a.get("enabled", True)),
        )
        db.add(m)
        await db.commit()
        return {"id": str(m.id)}


async def alert(a: dict) -> dict:
    from shc_api import store
    from shc_api.db import sessionmaker

    async with sessionmaker()() as db:
        created = await store.raise_alert(
            db,
            _uuid(a.get("home_id")),
            a["key"],
            a.get("severity", "warn"),
            a["title"],
            a.get("detail", ""),
            a.get("source", "e2e"),
        )
        await db.commit()
        return {"id": str(created.id) if created else None}


async def resolve(a: dict) -> dict:
    from shc_api import store
    from shc_api.db import sessionmaker

    async with sessionmaker()() as db:
        done = await store.resolve_alert(db, a["key"])
        await db.commit()
        return {"resolved": [str(x.id) for x in done]}


async def heartbeat(a: dict) -> dict:
    """The worker's heartbeat as worker/core.py writes it (now), or none again with {"clear": true}: the
    overview then shows "Achtergronddienst" as measured healthy, or as "no heartbeat" like before."""
    from shc_api.db import sessionmaker
    from shc_api.store import set_setting

    value = None if a.get("clear") else datetime.now(UTC).isoformat()
    async with sessionmaker()() as db:
        await set_setting(db, "worker_heartbeat", value)
        await db.commit()
    return {"worker_heartbeat": value}


async def readings(a: dict) -> dict:
    """A series of readings for one device and metric, as the worker stores them (store.ingest_reading):
    {"device_id": …, "metric": "temperature", "unit": "°C", "points": [[minutes_ago, value_or_null], …]}.
    A null value is a reading without a value (shown as "Geen meting", never as 0)."""
    from shc_api.db import sessionmaker
    from shc_api.store import ingest_reading

    now = datetime.now(UTC).replace(microsecond=0)
    device_id, metric, unit = uuid.UUID(a["device_id"]), a["metric"], a.get("unit", "°C")
    added = 0
    async with sessionmaker()() as db:
        for minutes_ago, value in a["points"]:
            at = now - timedelta(minutes=float(minutes_ago))
            eid = f"e2e-{uuid.uuid4().hex}"
            added += int(await ingest_reading(db, device_id, metric, value, unit, at, event_id=eid))
        await db.commit()
    return {"added": added, "now": now.isoformat()}


COMMANDS = {
    "device": device,
    "mapping": mapping,
    "alert": alert,
    "resolve": resolve,
    "heartbeat": heartbeat,
    "readings": readings,
}


async def main(cmd: str, raw: str) -> dict:
    from shc_api.db import dispose

    try:
        return await COMMANDS[cmd](json.loads(raw or "{}"))
    finally:
        await dispose()


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"gebruik: seed.py {{{'|'.join(COMMANDS)}}} '<json>'", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "{}"))))
