"""Actieve experimenten en adaptieve regeling op eigen testdatabases."""

from datetime import UTC, datetime, timedelta

import pytest

from shc_api.db import sessionmaker
from shc_api.models import AdaptiveModelVersion, ClimateZone
from shc_api.store import ingest_reading

from .boiler_support import protect
from .test_inbedrijfstelling_groepen import change
from .test_inbedrijfstelling_handhaving import commissioning_state as commissioning_state
from .test_inbedrijfstelling_handhaving import set_gate, snapshot
from .test_inbedrijfstelling_klimaat import LIVING, PROFILE
from .test_inbedrijfstelling_klimaat import climate_rig as climate_rig


async def condition_code(rig, condition):
    if condition == "untested":
        await change(rig.units[LIVING], compatibility="untested", tested_at=None)
        return "device_not_tested"
    if condition in {"protected", "protected_gate_uit"}:
        await protect(rig.home_id, rig.units[LIVING])
        if condition == "protected_gate_uit":
            await set_gate(False)
        return "policy_denied"
    return None


@pytest.mark.parametrize("operation", ["create", "resume", "stop", "delete", "worker", "worker-paused"])
@pytest.mark.parametrize("condition", ["tested", "untested", "protected", "protected_gate_uit"])
async def test_experiment_vroege_controle_en_adapter(admin, climate_rig, operation, condition):
    rig = climate_rig
    zid = rig.zones[LIVING]
    await rig.schedule(LIVING, 24, "heat")
    profile = await admin.post(f"/homes/{rig.home_id}/profiles", json=PROFILE)
    assert profile.status_code == 200, profile.text
    pid = profile.json()["id"]
    now = datetime.now(UTC)
    payload = {"name": "Proefplanner", "zone_id": str(zid), "auto_apply": True, "variants": [
        {"label": "Nu", "start": (now - timedelta(hours=1)).isoformat(),
         "end": (now + timedelta(hours=1)).isoformat(), "profile_id": pid},
        {"label": "Later", "start": (now + timedelta(hours=1)).isoformat(),
         "end": (now + timedelta(hours=2)).isoformat(), "profile_id": pid},
    ]}
    path = f"/homes/{rig.home_id}/experiments"
    if operation != "create":
        created = await admin.post(path, json=payload)
        assert created.status_code == 200, created.text
        path = f"/experiments/{created.json()['id']}"
        if operation in {"stop", "delete", "worker-paused"}:
            await rig.w._experiments_round(now, rig.homes)
            result = (await admin.get(path)).json()
            assert result["status"] == "running", result
            assert str((await rig.zone(LIVING)).profile_id) == pid
        if operation in {"resume", "worker-paused"}:
            paused = await admin.post(path + "/pause")
            assert paused.status_code == 200, paused.text

    code = await condition_code(rig, condition)
    before = await snapshot()
    if operation in {"worker", "worker-paused"}:
        moment = now + timedelta(hours=3) if operation == "worker-paused" else now
        await rig.w._experiments_round(moment, rig.homes)
        if not code:
            profile_id = (await rig.zone(LIVING)).profile_id
            assert (str(profile_id) if profile_id else None) == (pid if operation == "worker" else None)
    else:
        if operation == "create":
            result = await admin.post(path, json=payload)
        elif operation == "delete":
            result = await admin.delete(path)
        else:
            result = await admin.post(path + f"/{operation}")
        assert result.status_code == (409 if code else (204 if operation == "delete" else 200)), result.text
        if code:
            assert result.json()["detail"]["code"] == code
        elif operation in {"stop", "delete"}:
            assert (await rig.zone(LIVING)).profile_id is None
        else:
            await rig.w._experiments_round(now, rig.homes)
            assert str((await rig.zone(LIVING)).profile_id) == pid
    if code:
        assert await snapshot() == before, "De geweigerde planner wijzigde toch configuratie."
    assert await rig.commands(LIVING) == []
    await rig.round()
    await rig.run_commands()
    if code:
        assert await rig.commands(LIVING) == []
        assert rig.fakes[LIVING].calls == []
    else:
        assert len(rig.fakes[LIVING].calls) == 1
        assert (await rig.commands(LIVING))[0].status == "confirmed"


@pytest.mark.parametrize("condition", ["tested", "untested", "protected"])
async def test_adaptief_passieve_fit_en_bewaakte_activering(admin, climate_rig, condition):
    rig = climate_rig
    zid, unit = rig.zones[LIVING], rig.units[LIVING]
    await rig.schedule(LIVING, 23, "heat")
    code = await condition_code(rig, condition)
    at = datetime.now(UTC) - timedelta(hours=30)
    temp = 18.0
    async with sessionmaker()() as db:
        for i in range(110):
            target = 20.0 if i % 16 < 8 else 23.0
            moment = at + timedelta(minutes=15 * i)
            await ingest_reading(db, rig.units[f"sensor:{LIVING}"], "temperature", temp, "°C", moment)
            await ingest_reading(db, unit, "target_c", target, "°C", moment)
            temp += (0.4 * (target - temp) - 0.1) * 0.25
        await db.commit()
    fit = await admin.post(f"/zones/{zid}/adaptive/fit")
    assert fit.status_code == 200 and fit.json()["params"]["usable"], fit.text
    assert fit.json()["active"] is False
    before = await snapshot()
    result = await admin.post(f"/zones/{zid}/adaptive/activate", json={"model_id": fit.json()["id"]})
    assert result.status_code == (409 if code else 200), result.text
    if code:
        assert result.json()["detail"]["code"] == code
        assert await snapshot() == before
    else:
        assert result.json()["enabled"] is True
        assert result.json()["models"][0]["active"] is True
    await rig.round()
    await rig.run_commands()
    assert len(rig.fakes[LIVING].calls) == int(code is None)
    if not code:
        assert (await rig.commands(LIVING))[0].status == "confirmed"


@pytest.mark.parametrize("condition", ["tested", "untested", "protected"])
async def test_adaptief_uitschakelen_wijzigt_een_actieve_regeling(admin, climate_rig, condition):
    rig = climate_rig
    zid = rig.zones[LIVING]
    await rig.schedule(LIVING, 23, "heat")
    async with sessionmaker()() as db:
        zone = await db.get(ClimateZone, zid)
        zone.adaptive_enabled = True
        db.add(AdaptiveModelVersion(zone_id=zid, version=1, params={"usable": True}, active=True))
        await db.commit()
    code = await condition_code(rig, condition)
    before = await snapshot()
    result = await admin.post(f"/zones/{zid}/adaptive/disable")
    assert result.status_code == (409 if code else 200), result.text
    if code:
        assert result.json()["detail"]["code"] == code, result.text
        assert await snapshot() == before
    else:
        assert result.json()["enabled"] is False
        assert result.json()["models"][0]["active"] is False
    await rig.round()
    await rig.run_commands()
    assert len(rig.fakes[LIVING].calls) == int(code is None)
