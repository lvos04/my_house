"""Boilerweigeringen bereiken de echte HTTP-ingangen met geldige autorisatie."""

import uuid

import pytest
from sqlalchemy import select

from shc_api.db import sessionmaker
from shc_api.models import ClimateZone

from .test_api import add_device


@pytest.mark.parametrize("field", ["unit_device_id", "sensor_device_id"])
@pytest.mark.parametrize("kind,role,status", [
    ("energy_socket", "boiler", 409),
    ("ac_unit", "boiler", 409),
    ("zigbee_sensor", "boiler", 409),
    ("light_group", None, 422),
])
async def test_zone_patch_weigert_verkeerd_of_beschermd_doel(admin, home, field, kind, role, status):
    zone = (await admin.get(f"/homes/{home['id']}/zones")).json()[0]
    target = await add_device(home["id"], kind, "Testdoel", role=role)
    before = {f: zone[f] for f in ("unit_device_id", "sensor_device_id", "name")}
    response = await admin.c.patch(
        f"/api/v1/zones/{zone['id']}", json={field: str(target), "name": "Mag niet worden opgeslagen"},
        headers=admin.h(),
    )
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == ("policy_denied" if status == 409 else "validation_error")
    after = (await admin.get(f"/zones/{zone['id']}")).json()
    assert {f: after[f] for f in before} == before


async def test_zone_patch_ac_unit_en_sensor_en_ontkoppelen(admin, home):
    zone = (await admin.get(f"/homes/{home['id']}/zones")).json()[0]
    unit = await add_device(home["id"], "ac_unit", "Testairco")
    sensor = await add_device(home["id"], "zigbee_sensor", "Testsensor")
    for body in (
        {"unit_device_id": str(unit), "sensor_device_id": str(sensor)},
        {"unit_device_id": None, "sensor_device_id": None},
    ):
        response = await admin.c.patch(f"/api/v1/zones/{zone['id']}", json=body, headers=admin.h())
        assert response.status_code == 200, response.text
        async with sessionmaker()() as db:
            saved = (await db.execute(select(ClimateZone).where(ClimateZone.id == uuid.UUID(zone["id"])))).scalar_one()
            for field, value in body.items():
                assert str(getattr(saved, field)) == str(value)


@pytest.mark.parametrize("field,kind", [("unit_device_id", "zigbee_sensor"), ("sensor_device_id", "ac_unit")])
async def test_zone_patch_unit_en_sensor_zijn_niet_uitwisselbaar(admin, home, field, kind):
    zone = (await admin.get(f"/homes/{home['id']}/zones")).json()[0]
    target = await add_device(home["id"], kind, "Verkeerde soort")
    response = await admin.c.patch(f"/api/v1/zones/{zone['id']}", json={field: str(target)}, headers=admin.h())
    assert response.status_code == 422 and response.json()["detail"]["code"] == "validation_error"
    after = (await admin.get(f"/zones/{zone['id']}")).json()
    assert after[field] == zone[field]
