"""Lever synthetische meterstanden aan de normale gegevensinvoer in een proefcontainer."""

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


async def report_reading(source):
    from shc_api.db import dispose, sessionmaker
    from shc_api.models import Home, Integration, SensorReading
    from shc_api.worker import Worker
    from shc_integrations.base import DeviceInfo, Reading
    from sqlalchemy import JSON, null, select, update

    identity = "proefmeter-735"
    async with sessionmaker()() as db:
        if source:
            integration = Integration(home_id=(await db.scalars(select(Home.id))).one(),
                                      provider="homewizard", identity=identity, name="Proefmeter",
                                      enabled=False, config={"product_type": "HWE-P1"})
            db.add(integration)
            await db.commit()
        else:
            integration = (await db.scalars(select(Integration).where(Integration.identity == identity))).one()

    at = datetime.now(UTC)
    worker = Worker()
    if source:
        raw_values = {"sql-null": null(), "json-null": JSON.NULL, "veld-ontbreekt": {}, "veld-nul": {"v": 0}}
        events = [DeviceInfo(identity, "p1_meter", "Proefmeter", at)]
        for label in raw_values:
            events.append(Reading(identity, "power_w", 0.0, "W", at, event_id=label))
        await worker.handle_events(integration, events)
        async with sessionmaker()() as db:
            for label, raw in raw_values.items():
                changed = await db.execute(update(SensorReading).where(SensorReading.event_id == label).values(raw=raw))
                assert changed.rowcount == 1
            await db.commit()
    else:
        await worker.handle_events(integration, [Reading(identity, "power_w", 12.5, "W", at, event_id="doelmeting")])
    await dispose()


if __name__ == "__main__":
    from shc_allinone.supervisor import service_env

    os.environ.update(service_env())
    source = sys.argv[1] == "bron"
    if source:
        # Een aantoonbaar lege bronmap is een voorwaarde van de verhuiswizard.
        Path(os.environ["SHC_ZIGBEE2MQTT_DIR"]).mkdir(mode=0o700, exist_ok=True)
    asyncio.run(report_reading(source))
