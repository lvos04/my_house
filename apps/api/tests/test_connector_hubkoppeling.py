"""Schrijfbereik van de gekoppelde Hub en de gezamenlijke parametergrens."""

import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select

from shc_api.db import sessionmaker
from shc_api.models import ConnectorChange, Device, DeviceBinding, LogicalRole

from .test_connector_routes import proef as proef
from .test_connector_verificatie import b64, canonical
from .test_hub_alleen_lezen import koppel as koppel

PREFIX = "/connector/v1"


async def test_gekoppelde_hub_alleen_met_gepinde_handtekening(koppel, proef):
    client, koppel_client = koppel
    hub, _ = await koppel_client(client)
    telefoon, _ = await koppel_client(client, "admin", "phone")
    andere_hub, _ = await koppel_client(client)

    async def post(route, body, headers, idem=None):
        return await client.post(f"{PREFIX}/{route}", json=body,
                                 headers={**headers, "Idempotency-Key": idem or str(uuid.uuid4())})

    voorstel = proef.request()
    vreemd = {**voorstel}
    del vreemd["signature"]
    vreemd["signature"] = b64(Ed25519PrivateKey.generate().sign(canonical(vreemd)))
    for route in ("propose", "apply"):
        assert (await post(route, voorstel, {})).status_code == 401
        assert (await post(route, voorstel, telefoon)).status_code == 403
        for body in (vreemd, {k: v for k, v in voorstel.items() if k != "signature"}):
            assert (await post(route, body, hub)).status_code == 403
    assert (await post("rollback", voorstel, hub)).status_code == 403
    stekker = proef.request(action="socket.relay.propose", target={"device_id": str(uuid.uuid4())}, params={"on": True})
    assert (await post("propose", stekker, hub)).status_code == 403
    assert await proef.count() == 0

    antwoord = await post("propose", voorstel, hub)
    assert antwoord.status_code == 200
    preview = antwoord.json()
    uitvoeren, idem = proef.request(preview=preview), str(uuid.uuid4())
    assert (await post("apply", uitvoeren, andere_hub)).status_code == 409
    eerste = await post("apply", uitvoeren, hub, idem)
    herhaald = await post("apply", uitvoeren, hub, idem)
    assert eerste.status_code == herhaald.status_code == 202
    assert eerste.json() == herhaald.json()
    assert await proef.count() == 1
    await proef.work()
    bewijs = f"{PREFIX}/evidence/{eerste.json()['evidence_id']}"
    assert (await client.get(bewijs, headers=andere_hub)).status_code == 404
    data = (await client.get(bewijs, headers=hub)).json()
    assert (data["status"], data["before"]["target_c"], data["after"]["target_c"]) == ("confirmed", 20, 22)
    assert len(proef.unit.commands) == 1


@pytest.mark.parametrize("beschermd", [True, False])
async def test_hub_doelcontrole_geldt_op_alle_drie_routes(koppel, proef, beschermd):
    client, nieuw = koppel
    hub, _ = await nieuw(client)
    async with sessionmaker()() as db:
        if beschermd:
            role = LogicalRole(home_id=proef.home_id, key="boiler", label="Proefrol", protected=True)
            db.add(role)
            await db.flush()
            db.add(DeviceBinding(role_id=role.id, device_id=proef.device_id))
        else:
            (await db.get(Device, proef.device_id)).compatibility = "untested"
        await db.commit()
    weigeringen = [await client.get(f"{PREFIX}/discover", headers=hub,
                                    params={"action_id": "zone.command", "target_id": str(proef.zone_id)})]
    for route in ("propose", "apply"):
        weigeringen.append(await client.post(f"{PREFIX}/{route}", json=proef.request(),
                                             headers={**hub, "Idempotency-Key": str(uuid.uuid4())}))
    for antwoord in weigeringen:
        assert (antwoord.status_code, antwoord.json()["code"]) == (
            409, "policy_denied" if beschermd else "device_not_tested",
        )
    assert await proef.count() == 0


async def test_schema_bytebeperking_voor_opslag_ook_bij_100_blokken(proef):
    data = (await proef.admin.c.get(f"{PREFIX}/discover", params={
        "action_id": "zone.schedule.propose", "target_id": str(proef.zone_id),
    })).json()
    assert data["checked_target"] == {"zone_id": str(proef.zone_id)}
    [actie] = data["actions"]
    assert actie["params_schema"]["x-max-canonical-bytes"] == 16_384
    blocks = [{"days": list(range(7)), "start": f"{i // 60:02}:{i % 60:02}", "target_c": 22,
               "mode": "heat", "label": "é" * 50} for i in range(100)]
    params = {"blocks": blocks}
    assert len(canonical(params)) == 18_012

    async def voorstel():
        return await proef.post("propose", proef.request(action=actie["action_id"], params=params))

    for grootte in (18_012, 16_385, 16_384):
        teveel = len(canonical(params)) - grootte
        for block in blocks:
            n = min(teveel, block["label"].count("é"))
            block["label"] = block["label"].replace("é", "a", n)
            teveel -= n
        assert len(canonical(params)) == grootte
        antwoord = await voorstel()
        if grootte > 16_384:
            assert (antwoord.status_code, antwoord.json()["code"]) == (422, "parameters_ongeldig")
            assert antwoord.json()["details"]["fouten"] == [
                {"veld": "parameters", "code": "te_groot", "fout": "hooguit 16384 bytes"},
            ]
            async with sessionmaker()() as db:
                assert await db.scalar(select(func.count()).select_from(ConnectorChange).where(
                    ConnectorChange.proposal_key == uuid.UUID(proef.key_id))) == 0
        else:
            assert antwoord.status_code == 200 and antwoord.json()["concept_only"] is True


async def test_herhaalde_hubbon_blijft_onder_de_inbedrijfstellingsgate(koppel, proef):
    client, nieuw = koppel
    hub, _ = await nieuw(client)
    voorstel = proef.request()
    kop = {**hub, "Idempotency-Key": str(uuid.uuid4())}
    uitvoerkop = {**hub, "Idempotency-Key": str(uuid.uuid4())}
    r = await client.post(f"{PREFIX}/propose", json=voorstel, headers=kop)
    assert r.status_code == 200
    uitvoering = proef.request(preview=r.json())
    r = await client.post(f"{PREFIX}/apply", json=uitvoering, headers=uitvoerkop)
    assert r.status_code == 202
    async with sessionmaker()() as db:
        (await db.get(Device, proef.device_id)).compatibility = "untested"
        await db.commit()
    for route, body, headers in (("propose", voorstel, kop), ("apply", uitvoering, uitvoerkop)):
        r = await client.post(f"{PREFIX}/{route}", json=body, headers=headers)
        assert (r.status_code, r.json()["code"]) == (409, "device_not_tested")
    await proef.work()
    assert await proef.count() == 1 and not proef.unit.commands
