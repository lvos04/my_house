"""Schrijfbereik van de gekoppelde Hub en de gezamenlijke parametergrens."""

import uuid
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select

from shc_api.db import sessionmaker
from shc_api.models import ApiClient, ConnectorChange, Device, DeviceBinding, LogicalRole, User, utcnow

from .conftest import ADMIN_PW
from .test_connector_routes import proef as proef
from .test_connector_verificatie import b64, canonical
from .test_hub_alleen_lezen import koppel as koppel
from .test_ws_security_accounts import _login, _user
from .test_ws_security_pairing import _code, _redeem

PREFIX = "/connector/v1"


async def test_gekoppelde_hub_alleen_met_gepinde_handtekening(koppel, proef, app):
    client, koppel_client = koppel
    hub, _ = await koppel_client(client)
    code = await _code(proef.admin, role="admin", password=ADMIN_PW)
    koppeling = await _redeem(app, code["code"], "Lokale beheertelefoon")
    assert koppeling.status_code == 200
    gegevens = koppeling.json()
    assert (gegevens["client"]["role"], gegevens["client"]["purpose"]) == ("admin", "phone")
    telefoon = {"Authorization": f"Bearer {gegevens['token']}"}
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


async def test_lokaal_herstel_oorspronkelijk_hubvoorstel(koppel, proef):
    client, nieuw = koppel
    hub, _ = await nieuw(client)
    headers = {**hub, "Idempotency-Key": str(uuid.uuid4())}
    r = await client.post(f"{PREFIX}/propose", json=proef.request(), headers=headers)
    assert r.status_code == 200
    plan = r.json()
    headers["Idempotency-Key"] = str(uuid.uuid4())
    r = await client.post(f"{PREFIX}/apply", json=proef.request(preview=plan), headers=headers)
    assert r.status_code == 202
    await proef.work()
    assert proef.unit.state["SetTem"] == 22

    pad = f"/api/v1/system/hub-recovery/{plan['proposal_id']}"
    eigenaar = proef.admin
    gelezen = await eigenaar.c.get(pad)
    assert gelezen.status_code == 200
    bewijs = gelezen.json()
    herstel = bewijs["rollback_preview"]
    assert bewijs["id"] == plan["proposal_id"] and bewijs["target"] == {"zone_id": str(proef.zone_id)}
    assert herstel["parameters"] == {"target_c": 20}
    assert (await client.get(pad, headers=hub)).status_code == 403
    assert (await client.post(pad, json={}, headers=hub)).status_code == 403
    naam = f"beheer-{uuid.uuid4().hex[:8]}"
    await _user(eigenaar, naam, role="admin")
    andere = await _login(client, naam)
    assert (await andere.c.get(pad)).status_code == 404
    kop = {**eigenaar.h(), "Idempotency-Key": str(uuid.uuid4())}
    akkoord = {"preview_hash": herstel["preview_hash"], "password": ADMIN_PW}
    r = await andere.c.post(pad, headers={**andere.h(), "Idempotency-Key": str(uuid.uuid4())}, json=akkoord)
    assert r.status_code == 404
    assert (await eigenaar.c.post(pad, headers=kop, json={"preview_hash": herstel["preview_hash"]})).status_code == 422
    assert (await eigenaar.c.post(pad, headers=kop, json={**akkoord, "password": naam})).status_code == 403
    r = await eigenaar.c.post(pad, headers=kop, json={**akkoord, "preview_hash": plan["preview_hash"]})
    assert r.status_code == 409
    assert await proef.count() == 1

    eerste = await eigenaar.c.post(pad, headers=kop, json=akkoord)
    herhaald = await eigenaar.c.post(pad, headers=kop, json=akkoord)
    assert eerste.status_code == herhaald.status_code == 202
    assert eerste.json() == herhaald.json() and eerste.json()["evidence_id"] == plan["proposal_id"]
    await proef.work()
    data = (await eigenaar.c.get(pad)).json()
    for veld in (
        "id", "command_id", "before", "before_hash", "before_observed_at", "after", "after_hash", "after_observed_at",
    ):
        assert data[veld] == bewijs[veld]
    assert data["rollback_status"] == "confirmed" and data["rollback_command_id"] == eerste.json()["command_id"]
    assert data["rollback_after"] == data["before"] and data["rollback_after_hash"] == data["before_hash"]
    assert proef.unit.state["SetTem"] == 20 and len(proef.unit.commands) == await proef.count() == 2


async def test_hub_setpoint_zonder_bevoegde_herstelbeheerder_geeft_501(koppel, proef):
    client, nieuw = koppel
    hub, client_id = await nieuw(client)
    kop = {**hub, "Idempotency-Key": str(uuid.uuid4())}
    voorstel = proef.request()
    r = await client.post(f"{PREFIX}/propose", json=voorstel, headers=kop)
    assert r.status_code == 200
    uitvoering = proef.request(preview=r.json())
    async with sessionmaker()() as db:
        koppeling = await db.get(ApiClient, client_id)
        beheerder = await db.get(User, koppeling.user_id)
        beheerder.role = "viewer"
        await db.commit()
        try:
            proef.key_id = str(uuid.uuid4())
            for route, body, headers in (
                ("propose", proef.request(), {**hub, "Idempotency-Key": str(uuid.uuid4())}),
                ("propose", voorstel, kop),
                ("apply", uitvoering, {**hub, "Idempotency-Key": str(uuid.uuid4())}),
            ):
                r = await client.post(f"{PREFIX}/{route}", json=body, headers=headers)
                assert (r.status_code, r.json().get("code")) == (501, "capability_unavailable")
                assert r.json()["details"]["reason"] == "rollback_unavailable"
            assert await proef.count() == 0 and not proef.unit.commands
        finally:
            beheerder.role = "admin"
            await db.commit()


async def test_worker_verzendt_geen_hub_setpoint_zonder_herstelbeheerder(koppel, proef):
    client, nieuw = koppel
    hub, client_id = await nieuw(client)
    kop = {**hub, "Idempotency-Key": str(uuid.uuid4())}
    r = await client.post(f"{PREFIX}/propose", json=proef.request(), headers=kop)
    assert r.status_code == 200
    aanvraag = proef.request(preview=r.json())
    kop["Idempotency-Key"] = str(uuid.uuid4())
    r = await client.post(f"{PREFIX}/apply", json=aanvraag, headers=kop)
    assert r.status_code == 202
    pad = f"{PREFIX}/evidence/{r.json()['evidence_id']}"
    async with sessionmaker()() as db:
        koppeling = await db.get(ApiClient, client_id)
        beheerder = await db.get(User, koppeling.user_id)
        beheerder.locked_until = utcnow() + timedelta(minutes=5)
        await db.commit()
        try:
            herhaald = await client.post(f"{PREFIX}/apply", json=aanvraag, headers=kop)
            assert (herhaald.status_code, herhaald.json().get("code")) == (501, "capability_unavailable")
            await proef.work()
            assert (await client.get(pad, headers=hub)).json()["status"] == "failed"
            assert not proef.unit.commands and proef.unit.state["SetTem"] == 20
        finally:
            beheerder.locked_until = None
            await db.commit()


async def test_eerder_telefoonvoorstel_houdt_eigen_ondertekend_herstel(koppel, proef):
    client, nieuw = koppel
    telefoon, client_id = await nieuw(client, doel="phone")
    andere, _ = await nieuw(client, doel="phone")
    plan = await proef.propose()
    assert (await proef.post("apply", proef.request(preview=plan))).status_code == 202
    await proef.work()
    # Eerdere versies registreerden een ondertekend telefoonvoorstel op dezelfde manier onder de eigen client.
    async with sessionmaker()() as db:
        wijziging = await db.get(ConnectorChange, uuid.UUID(plan["proposal_id"]))
        wijziging.client = f"client:{client_id}"
        await db.commit()
    pad = f"{PREFIX}/evidence/{plan['proposal_id']}"
    assert (await proef.admin.c.get(f"/api/v1/system/hub-recovery/{plan['proposal_id']}")).status_code == 404
    bewijs = (await client.get(pad, headers=telefoon)).json()
    assert bewijs["status"] == "confirmed" and proef.unit.state["SetTem"] == 22
    herstel = proef.request(preview=plan, rollback=bewijs["rollback_preview"])
    assert (await client.get(pad, headers=andere)).status_code == 404
    r = await client.post(f"{PREFIX}/rollback", json=herstel, headers={**andere, "Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 409
    r = await client.post(f"{PREFIX}/rollback", json=herstel, headers={**telefoon, "Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 202
    await proef.work()
    na = (await client.get(pad, headers=telefoon)).json()
    assert na["rollback_status"] == "confirmed" and na["rollback_after"] == bewijs["before"]
    assert proef.unit.state["SetTem"] == 20 and len(proef.unit.commands) == 2


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
    algemeen = await proef.admin.c.get(f"{PREFIX}/discover")
    assert algemeen.status_code == 200 and set(algemeen.json()) == {"actions"}
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
