"""Health round: sensor staleness alerts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from .. import climate as cl
from ..db import sessionmaker
from ..models import ClimateZone, Device
from ..store import raise_alert, resolve_alert


class HealthMixin:
    async def health_round(self) -> None:
        now = datetime.now(UTC)
        async with sessionmaker()() as db:
            zones = (
                (await db.execute(select(ClimateZone).where(ClimateZone.sensor_device_id.is_not(None)))).scalars().all()
            )
            for z in zones:
                pol = cl.policy_with_defaults(z.policy)
                dev = await db.get(Device, z.sensor_device_id)
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
