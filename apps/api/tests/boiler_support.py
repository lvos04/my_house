"""Lokale testopstelling voor boilerketens; geen verbinding met apparaten."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from shc_api.db import sessionmaker
from shc_api.models import ClimateZone, CommandRecord, Device, DeviceBinding, LogicalRole
from shc_api.store import set_setting

from .test_api import add_device
from .ws_devices_support import new_home


async def protect(home_id, device_id):
    async with sessionmaker()() as db:
        role = (await db.execute(select(LogicalRole).where(
            LogicalRole.home_id == home_id, LogicalRole.key == "boiler",
        ))).scalar_one()
        db.add(DeviceBinding(role_id=role.id, device_id=device_id,
                             effective_from=datetime.now(UTC) - timedelta(days=1)))
        await db.commit()


async def commands(home_id):
    async with sessionmaker()() as db:
        return list((await db.execute(select(CommandRecord).where(CommandRecord.home_id == home_id))).scalars())


@pytest.fixture
async def boiler_rig(admin, home):
    hid = await new_home(admin, "Boilertest")
    ids = {}
    for key, kind, role in (
        ("boiler", "energy_socket", "boiler"),
        ("protected_group", "light_group", "boiler"),
        ("protected_scene", "scene", "boiler"),
        ("group", "light_group", None), ("scene", "scene", None),
        ("button", "button", None), ("unit", "ac_unit", None),
    ):
        ids[key] = await add_device(str(hid), kind, key, role=role,
                                   state={"mode": "heat", "power": True, "target_c": 20})
    zid = uuid.UUID((await admin.get(f"/homes/{hid}/zones")).json()[0]["id"])
    async with sessionmaker()() as db:
        z = await db.get(ClimateZone, zid)
        z.unit_device_id = ids["boiler"]  # Oude/importkoppeling passeert bewust niet de nieuwe PATCH-validatie.
        await set_setting(db, "safe_mode", False)
        await db.commit()
    return SimpleNamespace(home=hid, zone=zid, **ids)


def mapping(step, mode="lights", button="testknop"):
    return {"button": button, "event": "short_release", "mode": mode, "action": {"steps": [step]}}


async def mappings(admin, rig):
    return (await admin.get(f"/buttons/{rig.button}")).json()["mapping"]


async def set_online(device_id, online):
    async with sessionmaker()() as db:
        d = await db.get(Device, device_id)
        d.online = online
        await db.commit()
