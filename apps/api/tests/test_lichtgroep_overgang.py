"""De schakelmeettijd van een Hue-groep op de leesroutes en in het groepsbericht."""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from shc_api.models import Device
from shc_api.views.lights import group_view
from shc_api.worker import Worker

from .hue_overgang import groepsmeting
from .test_herstel_oude_export import pak_bronversie_uit
from .ws_devices_support import add_device, add_integration, fetch, new_home

METING = datetime(2025, 1, 1, 12, tzinfo=UTC)
BRON_ZONDER_ON_AT = "c296859a5a99d550b97367f93ac56d0db9cf9c89"


async def controleer_leesroutes(admin, woning, zones, verwacht):
    for versie in ("standaard", "2"):
        params = {"light_state_version": "2"} if versie == "2" else {}
        lijst = await admin.get(f"/homes/{woning}/lights", params=params)
        overzicht = await admin.get(f"/homes/{woning}/overview", params=params)
        assert lijst.status_code == overzicht.status_code == 200
        assert {z["id"] for z in overzicht.json()["zones"]} == zones
        zichtbaar = {k: v for k, v in verwacht.items() if versie == "2" or v[0] is not None}
        for groepen in (lijst.json()["groups"], overzicht.json()["lights"]):
            assert {
                g["id"]: (g["on"], datetime.fromisoformat(g["on_at"]) if g["on_at"] is not None else None)
                for g in groepen
            } == zichtbaar


async def zone_ids(admin, woning):
    antwoord = await admin.get(f"/homes/{woning}/zones")
    assert antwoord.status_code == 200 and antwoord.json()
    return {z["id"] for z in antwoord.json()}


@pytest.mark.parametrize(
    ("state", "stand"),
    [
        pytest.param({"on": True, "on_at": METING.isoformat()}, True, id="aan-met-meettijd"),
        pytest.param({"on": False, "on_at": METING.isoformat()}, False, id="uit-met-meettijd"),
        pytest.param({"on": True}, None, id="oude-aan-zonder-meettijd"),
        pytest.param({"on": False}, None, id="oude-uit-zonder-meettijd"),
        pytest.param({"on": False, "on_at": None}, None, id="lege-tijd"),
        pytest.param({"on": True, "on_at": "onleesbaar"}, None, id="ongeldige-tijd"),
        pytest.param({"on": True, "on_at": METING.date().isoformat()}, None, id="zonder-tijdzone"),
        pytest.param({"on": True, "on_at": datetime.max.replace(tzinfo=UTC).isoformat()}, None, id="toekomst"),
        pytest.param({"on_at": METING.isoformat()}, None, id="zonder-stand"),
        pytest.param({"on": "false", "on_at": METING.isoformat()}, None, id="ongeldige-stand"),
    ],
)
async def test_toestandstabel_lichtgroep(admin, state, stand):
    woning = await new_home(admin)
    groep = await add_device(
        woning, "light_group", "Proefgroep", provider="hue", state=state, last_seen=METING + timedelta(hours=1)
    )
    verwacht = (stand, METING if stand is not None else None)
    bericht = group_view(groep)
    assert (bericht["on"], bericht["on_at"]) == verwacht
    await controleer_leesroutes(admin, woning, await zone_ids(admin, woning), {str(groep.id): verwacht})


async def test_bestaande_lichtgroepen_na_bronupdate(admin, tmp_path):
    woning = await new_home(admin)
    zones = await zone_ids(admin, woning)
    brug = await add_integration(woning, "hue", "Proefbrug")
    groepen = [
        await add_device(
            woning,
            "light_group",
            naam,
            provider="hue",
            stable_identity=str(uuid4()),
            integration_id=brug.id,
            last_seen=None,
        )
        for naam in ("Eerder uit", "Zonder meting", "Referentie")
    ]
    eerder_uit, nooit_gemeten, referentie = groepen
    oud = pak_bronversie_uit(tmp_path, BRON_ZONDER_ON_AT)
    bronpaden = os.pathsep.join(str(oud / pad) for pad in ("apps/api/src", "integrations/src"))
    laatste = datetime.now(UTC) - timedelta(seconds=1)
    proces = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("hue_overgang.py")),
        str(brug.id),
        *(g.stable_identity for g in groepen),
        laatste.isoformat(),
        cwd=oud,
        env=dict(os.environ, PYTHONPATH=bronpaden),
    )
    assert await proces.wait() == 0
    for groep in (eerder_uit, nooit_gemeten):
        rij = await fetch(Device, groep.id)
        assert rij.state["on"] is False and "on_at" not in rij.state and rij.last_seen == laatste

    nieuw = Worker()
    vers = datetime.now(UTC)
    await nieuw.handle_events(brug, [groepsmeting(referentie.stable_identity, {"on": {"on": True}}, vers)])
    verwacht = {str(eerder_uit.id): (None, None), str(nooit_gemeten.id): (None, None), str(referentie.id): (True, vers)}
    await controleer_leesroutes(admin, woning, zones, verwacht)

    uit = datetime.now(UTC)
    await nieuw.handle_events(brug, [groepsmeting(nooit_gemeten.stable_identity, {"on": {"on": False}}, uit)])
    verwacht[str(nooit_gemeten.id)] = (False, uit)
    await controleer_leesroutes(admin, woning, zones, verwacht)
