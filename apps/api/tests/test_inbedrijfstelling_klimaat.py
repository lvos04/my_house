"""Klimaatconfiguratie, huidige teststatus en uitvoering met lokale nepunits."""

from datetime import UTC, datetime

import pytest

from .boiler_support import boiler_rig as boiler_rig
from .boiler_support import protect
from .test_inbedrijfstelling_groepen import change
from .test_inbedrijfstelling_handhaving import commissioning_state as commissioning_state
from .test_inbedrijfstelling_handhaving import set_gate, snapshot
from .test_ws_climate_worker import ALL_DAY, make_rig, sensor_value

LIVING = "Woonkamer"
BEDROOM = "Slaapkamer"
PROFILE = {"name": "Warm", "target_c": 23, "mode": "heat"}


@pytest.fixture
async def climate_rig(admin):
    rig = await make_rig(admin, living=("heat", 20), bedroom=("heat", 20), sensor=True)
    for name in (LIVING, BEDROOM):
        await change(rig.units[name], compatibility="tested", tested_at=datetime.now(UTC))
        await sensor_value(rig, name, 18)
    return rig


@pytest.mark.parametrize("route", ["schema", "beleid", "hervatten", "profiel", "profielwijziging"])
@pytest.mark.parametrize("condition", ["tested", "untested", "protected", "gate_uit", "protected_gate_uit"])
async def test_klimaatconfiguratie_tot_adapters(admin, climate_rig, route, condition):
    rig = climate_rig
    zone = rig.zones[LIVING]
    # Met gate aan, voordat de status van het doel verandert.
    await rig.schedule(LIVING, 23, "heat")
    response = await admin.post(f"/homes/{rig.home_id}/profiles", json=PROFILE)
    assert response.status_code == 200, response.text
    profile = response.json()["id"]
    if route == "profielwijziging":
        for name in (LIVING, BEDROOM):
            response = await admin.post(f"/zones/{rig.zones[name]}/profile", json={"profile_id": profile})
            assert response.status_code == 200, response.text
    if route == "hervatten":
        await rig.set_zone(LIVING, control="off")

    code = None
    if condition in {"untested", "gate_uit"}:
        await change(rig.units[LIVING], compatibility="untested", tested_at=None)
        if condition == "gate_uit":
            await set_gate(False)
        else:
            code = "device_not_tested"
    if condition in {"protected", "protected_gate_uit"}:
        await protect(rig.home_id, rig.units[LIVING])
        code = "policy_denied"
        if condition == "protected_gate_uit":
            await set_gate(False)

    before = await snapshot()
    if route == "schema":
        response = await admin.put(f"/zones/{zone}/schedule", json={"blocks": [
            {**ALL_DAY, "days": [-1] if code else ALL_DAY["days"], "target_c": 24, "mode": "heat"},
        ]})
    elif route == "beleid":
        body = {"plausible_min_c": 25, "plausible_max_c": 20} if code else {"band_c": 0.6}
        response = await admin.put(f"/zones/{zone}/policy", json=body)
    elif route == "hervatten":
        response = await admin.post(f"/zones/{zone}/resume")
    elif route == "profiel":
        response = await admin.post(f"/zones/{zone}/profile", json={"profile_id": profile})
    else:
        response = await admin.put(f"/profiles/{profile}", json={**PROFILE, "target_c": 24})
    assert response.status_code == (409 if code else 200), response.text
    if code:
        assert response.json()["detail"]["code"] == code, response.text
        assert await snapshot() == before
    else:
        assert await snapshot() != before
    assert await rig.commands(LIVING) == []
    assert rig.fakes[LIVING].calls == []
    await rig.round()
    await rig.run_commands()
    if code:
        assert await rig.commands(LIVING) == []
        assert rig.fakes[LIVING].calls == []
    else:
        calls = rig.fakes[LIVING].calls
        assert len(calls) == 1 and calls[0]["target_c"] > 20
        assert (await rig.commands(LIVING))[0].status == "confirmed"
    if route == "profielwijziging" and not code:
        assert len(rig.fakes[BEDROOM].calls) == 1


async def test_oem_regelt_bij_ongeteste_unit_en_schema_werkt_na_bevestiging(admin, climate_rig):
    rig = climate_rig
    await rig.schedule(LIVING, 23, "heat")
    await change(rig.units[LIVING], compatibility="untested", tested_at=None)
    cfg = (await admin.get(f"/zones/{rig.zones[LIVING]}/schedule")).json()
    await rig.round()
    await rig.run_commands()
    assert rig.fakes[LIVING].calls == []
    assert await rig.commands(LIVING) == []
    result = (await admin.get(f"/zones/{rig.zones[LIVING]}")).json()
    assert result["last_decision"] == "oem_fallback:device_not_tested", result
    assert result["control"] == "oem_fallback", result
    assert "nog niet getest" in result["notice"], result
    assert (await admin.get(f"/zones/{rig.zones[LIVING]}/schedule")).json() == cfg
    await change(rig.units[LIVING], compatibility="tested", tested_at=datetime.now(UTC))
    await rig.round()
    await rig.run_commands()
    assert len(rig.fakes[LIVING].calls) == 1
    assert (await rig.commands(LIVING))[0].status == "confirmed"


@pytest.mark.parametrize("status", ["tested", "untested"])
async def test_oude_zone_met_boilerstekker_kan_geen_schema_opslaan(admin, boiler_rig, status):
    r = boiler_rig
    await change(r.boiler, compatibility=status, tested_at=datetime.now(UTC) if status == "tested" else None)
    before = await snapshot()
    result = await admin.put(f"/zones/{r.zone}/schedule", json={"blocks": [
        {**ALL_DAY, "target_c": 23, "mode": "heat"},
    ]})
    assert result.status_code == 409, result.text
    assert result.json()["detail"]["code"] == "policy_denied", result.text
    assert await snapshot() == before


@pytest.mark.parametrize("operation", ["bind", "unbind", "name", "sensor", "outdoor", "sensor-tested"])
async def test_zonekoppeling_kan_regeling_niet_naar_ongetest_doel_verleggen(admin, climate_rig, operation):
    rig = climate_rig
    await rig.schedule(LIVING, 23, "heat")
    unit = rig.units[LIVING]
    if operation == "bind":
        unit = rig.units[BEDROOM]
    if operation != "sensor-tested":
        await change(unit, compatibility="untested", tested_at=None)
    body = {
        "bind": {"unit_device_id": str(unit)},
        "unbind": {"unit_device_id": None},
        "name": {"name": "Andere naam"},
        "sensor": {"sensor_device_id": str(rig.units[f"sensor:{BEDROOM}"])},
        "sensor-tested": {"sensor_device_id": str(rig.units[f"sensor:{BEDROOM}"])},
        "outdoor": {"outdoor_group": "Andere groep"},
    }[operation]
    before = await snapshot()
    result = await admin.c.patch(f"/api/v1/zones/{rig.zones[LIVING]}", json=body, headers=admin.h())
    activates = operation in {"bind", "sensor", "outdoor"}
    assert result.status_code == (409 if activates else 200), result.text
    if activates:
        assert result.json()["detail"]["code"] == "device_not_tested"
        assert await snapshot() == before
    else:
        assert await snapshot() != before
    assert await rig.commands(LIVING) == []
