"""Schrijf de waarnemingen van vóór de bronupdate met de toenmalige Hue-code."""

import asyncio
import sys
from datetime import datetime, timedelta
from inspect import getfile
from pathlib import Path
from uuid import UUID

from shc_integrations.base import StateUpdate
from shc_integrations.hue.model import HueModel

from shc_api.db import sessionmaker
from shc_api.models import Integration
from shc_api.worker import Worker


def groepsmeting(rid, velden, tijd):
    bron = HueModel()
    bron.load([{"id": rid, "type": "grouped_light", **velden}])
    (meting,) = [event for event in bron.snapshot(tijd) if isinstance(event, StateUpdate)]
    return meting


async def schrijf_oude_waarnemingen(brug, eerder_uit, nooit_gemeten, referentie, tijd):
    assert all(Path(getfile(cls)).is_relative_to(Path.cwd()) for cls in (HueModel, Worker))
    laatst_gezien = datetime.fromisoformat(tijd)
    async with sessionmaker()() as db:
        integratie = await db.get(Integration, UUID(brug))
    worker = Worker()
    await worker.handle_events(
        integratie, [groepsmeting(eerder_uit, {"on": {"on": False}}, laatst_gezien - timedelta(hours=1))]
    )
    await worker.handle_events(
        integratie,
        [
            groepsmeting(eerder_uit, {}, laatst_gezien),
            groepsmeting(nooit_gemeten, {"on": {}}, laatst_gezien),
            groepsmeting(referentie, {"on": {"on": True}}, laatst_gezien),
        ],
    )


if __name__ == "__main__":
    asyncio.run(schrijf_oude_waarnemingen(*sys.argv[1:]))
