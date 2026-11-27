"""Lokale contractproeven voor het ondertekende schrijfpad."""

import asyncio
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import delete, func, or_, select

from shc_api import backup as bk
from shc_api.connector_changes import digest
from shc_api.db import sessionmaker
from shc_api.errors import require_secret_box
from shc_api.hub_keys import parse_pasted_keyset
from shc_api.models import (
    ClimateZone,
    CommandRecord,
    ConnectorChange,
    ConnectorHubPin,
    Device,
    DeviceBinding,
    Home,
    Integration,
    LogicalRole,
    ScheduleBlock,
    utcnow,
)
from shc_api.worker import Worker

from .test_connector_verificatie import b64, canonical, iso, jwk, pasted
from .test_inbedrijfstelling_migratie import sql
from .test_ws_backup_format import temp_db

SIMULATORS = Path(__file__).resolve().parents[3] / "integrations/tests"


@pytest.mark.parametrize("route", ["discover", f"evidence/{uuid.uuid4()}"])
async def test_connectorlezingen_vragen_aanmelding(client, route):
    antwoord = await client.get(f"/connector/v1/{route}")
    assert antwoord.status_code == 401
    assert antwoord.json()["code"] == "unauthorized"
    assert set(antwoord.json()) == {"code", "message", "details", "trace_id"}


async def test_discover_geeft_precies_de_toegestane_acties(admin):
    antwoord = await admin.c.get("/connector/v1/discover")
    assert antwoord.status_code == 200
    assert [actie["action_id"] for actie in antwoord.json()["actions"]] == [
        "light.group.set", "light.scene.recall", "zone.command", "zone.resume", "zone.schedule.propose",
        "socket.relay.propose",
    ]


class Proefconnector:
    def __init__(self, admin, key, unit, home_id, device_id, zone_id):
        self.admin = admin
        self.key = key
        self.unit = unit
        self.home_id = home_id
        self.device_id = device_id
        self.zone_id = zone_id
        self.key_id = str(uuid.uuid4())

    def request(self, *, action="zone.command", target=None, params=None, preview=None, claim="R0",
                risk="R2", etag=None, approval=True, rollback=None):
        now = utcnow().replace(microsecond=0)
        policy_hash = digest(["beleid"])
        proposal = {
            "idempotency_key": self.key_id,
            "action_id": action,
            "target": target if target is not None else {"zone_id": str(self.zone_id)},
            "parameters": params if params is not None else {"target_c": 22},
            "expected_version": etag or (preview["resource_etag"] if preview else "eigenaar"),
            "actor": {"type": "ai", "id": "proef"},
            "risk_claim": claim,
        }
        body = {
            "proposal": proposal,
            "approval": None,
            "policy_decision": {"risk_class": risk, "rule_ids": ["proefregel"], "policy_hash": policy_hash},
            "issued_at": iso(now),
            "expires_at": iso(now + timedelta(minutes=1)),
            "nonce": str(uuid.uuid4()),
        }
        if preview and approval:
            body["approval"] = {
                "approval_id": str(uuid.uuid4()), "request_id": proposal["idempotency_key"],
                "preview_hash": (rollback or preview)["preview_hash"], "policy_hash": policy_hash, "risk_class": risk,
                "resource_etag": preview["resource_etag"], "approved_by": "proefbeheer",
                "approved_at": iso(now), "expires_at": iso(now + timedelta(minutes=1)),
                "one_time_nonce": str(uuid.uuid4()), "backup_checkpoint_id": None,
                "action_scope": {"action_id": action, "target": proposal["target"],
                                 "parameters_sha256": digest(
                                     rollback["parameters"] if rollback else proposal["parameters"],
                                 )},
            }
        return self.sign(body)

    def sign(self, body):
        body.pop("signature", None)
        body["signature"] = b64(self.key.sign(canonical(body)))
        return body

    async def post(self, route, body, idem=None):
        return await self.admin.c.post(
            f"/connector/v1/{route}",
            headers={**self.admin.h(), "Idempotency-Key": idem or str(uuid.uuid4())},
            json=body,
        )

    async def propose(self, **options):
        answer = await self.post("propose", self.request(**options))
        assert answer.status_code == 200, answer.json()
        return answer.json()

    async def evidence(self, identity):
        answer = await self.admin.c.get(f"/connector/v1/evidence/{identity}")
        assert answer.status_code == 200
        return answer.json()

    async def count(self):
        async with sessionmaker()() as db:
            return await db.scalar(select(func.count()).select_from(CommandRecord).where(
                CommandRecord.device_id == self.device_id,
            ))

    async def work(self):
        await self.worker.process_commands({self.home_id})


@pytest.fixture
async def proef(admin, monkeypatch):
    monkeypatch.syspath_prepend(str(SIMULATORS))
    from fake_gree import DEVICE_KEY, MAC, start_unit
    from shc_integrations.gree import GreeAdapter

    unit, transport, port = await start_unit(state={"Pow": 1})
    key = Ed25519PrivateKey.generate()
    jwt = jwk(Ed25519PrivateKey.generate(), "jwt")
    policy = jwk(key, "policy")
    context = None
    keyset = parse_pasted_keyset(pasted([jwt], policy))
    try:
        async with sessionmaker()() as db:
            await db.execute(delete(ConnectorHubPin))
            db.add(ConnectorHubPin(id=1, jwt_keys=[jwt], policy_keys=[policy], fingerprint=keyset.fingerprint,
                                   rotation_sequence=0, accept_from=utcnow() - timedelta(minutes=1)))
            home = Home(name="Connectorsimulator")
            db.add(home)
            await db.flush()
            integration = Integration(
                name="Klimaatsimulator", home_id=home.id, provider="gree",
                config={"host": transport.get_extra_info("sockname")[0], "port": port, "mac": MAC, "timeout_s": 0.15},
                credentials_enc=require_secret_box().encrypt({"key": DEVICE_KEY}),
            )
            db.add(integration)
            await db.flush()
            dev = Device(name="Simulatie", home_id=home.id, integration_id=integration.id, kind="ac_unit",
                         provider="gree", stable_identity=MAC, compatibility="tested", online=True,
                         state={"power": True, "mode": "heat", "target_c": 20})
            db.add(dev)
            await db.flush()
            zone = ClimateZone(home_id=home.id, name="Simulatiezone", unit_device_id=dev.id, control="manual",
                               override_until=utcnow() + timedelta(hours=1))
            db.add(zone)
            await db.commit()
            context = Proefconnector(admin, key, unit, home.id, dev.id, zone.id)
            context.worker = Worker()
            context.worker.adapters[integration.id] = GreeAdapter(integration.config, {"key": DEVICE_KEY})
        yield context
    finally:
        transport.close()
        async with sessionmaker()() as db:
            await db.execute(delete(ConnectorHubPin))
            if context is not None:
                await db.execute(delete(ConnectorChange).where(
                    or_(
                        ConnectorChange.target["zone_id"].astext == str(context.zone_id),
                        ConnectorChange.target["group_id"].astext == str(context.device_id),
                        ConnectorChange.target["scene_id"].astext == str(context.device_id),
                    ),
                ))
                await db.execute(delete(Home).where(Home.id == context.home_id))
            await db.commit()


async def test_apply_idempotent_en_rollback_met_readback(proef):
    preview = await proef.propose()
    assert preview["before"]["target_c"] == 20
    assert preview["rollback_recipe"] == {"target_c": 20}
    assert await proef.count() == 0
    req, idem = proef.request(preview=preview), str(uuid.uuid4())
    first, repeat = await asyncio.gather(proef.post("apply", req, idem), proef.post("apply", req, idem))
    assert first.status_code == repeat.status_code == 202
    assert first.json() == repeat.json()
    assert sum(r.headers.get("idempotent-replay") == "true" for r in (first, repeat)) == 1
    assert await proef.count() == 1
    assert (await proef.evidence(first.json()["command_id"]))["status"] == "queued"
    await proef.work()
    assert len(proef.unit.commands) == 1
    assert proef.unit.state["SetTem"] == 22
    done = await proef.evidence(preview["proposal_id"])
    assert done["status"] == "confirmed" and done["after"]["target_c"] == 22
    assert done["after_hash"] != done["before_hash"]
    restored = await proef.post("rollback", proef.request(preview=preview, rollback=done["rollback_preview"]))
    assert restored.status_code == 202, restored.json()
    await proef.work()
    assert len(proef.unit.commands) == 2
    assert proef.unit.state["SetTem"] == 20
    proof = await proef.evidence(restored.json()["command_id"])
    assert proof["rollback_status"] == "confirmed"
    assert proof["rollback_after"] == proof["before"]
    assert proof["rollback_after_hash"] == proof["before_hash"]
    async with sessionmaker()() as db:
        zone = await db.get(ClimateZone, proef.zone_id)
        assert zone.control == "manual" and zone.desired_target_c is None


@pytest.mark.parametrize("target_kind", ["apparaat", "zone"])
async def test_boiler_ook_voor_geldig_ondertekende_ai_geweigerd(proef, target_kind):
    async with sessionmaker()() as db:
        role = LogicalRole(home_id=proef.home_id, key="boiler", label="Boiler", protected=True)
        db.add(role)
        await db.flush()
        db.add(DeviceBinding(role_id=role.id, device_id=proef.device_id))
        await db.commit()
    options = ({"action": "light.group.set", "target": {"group_id": str(proef.device_id)}, "params": {"on": False}}
               if target_kind == "apparaat" else {})
    answer = await proef.post("apply", proef.request(**options))
    assert answer.status_code == 409
    assert answer.json()["code"] == "policy_denied"
    assert await proef.count() == 0


@pytest.fixture
async def stekker(proef):
    async with sessionmaker()() as db:
        device = Device(home_id=proef.home_id, provider="homewizard", kind="energy_socket",
                        name="Proefstekker", stable_identity=uuid.uuid4().hex, compatibility="tested")
        db.add(device)
        await db.commit()
        device_id = device.id
    yield device_id
    async with sessionmaker()() as db:
        await db.execute(delete(ConnectorChange).where(ConnectorChange.target["device_id"].astext == str(device_id)))
        await db.commit()


@pytest.mark.parametrize(("role", "protected"), [("boiler", False), ("boiler_energy", False), ("meetpunt", True)])
async def test_stekkervoorstel_blijft_concept_en_volgt_de_actuele_rol(proef, stekker, role, protected):
    target = {"device_id": str(stekker)}
    options = {"action": "socket.relay.propose", "target": target, "params": {"on": False}, "risk": "R1"}
    body, idem = proef.request(**options), str(uuid.uuid4())
    antwoord = await proef.post("propose", body, idem)
    assert antwoord.status_code == 200
    preview = antwoord.json()
    assert preview["status"] == "proposed" and preview["concept_only"] is True
    assert preview["blast_radius"] == [target] and preview["diff"]["proposed"] == {"on": False}
    assert preview["rollback_recipe"] is None
    assert (await proef.post("propose", body, idem)).json() == preview
    licht = proef.request(action="light.group.set", target={"group_id": str(stekker)}, params={"on": False})
    denied = await proef.post("propose", licht)
    assert denied.status_code == 409 and denied.json()["code"] == "policy_denied"
    for route in ("apply", "rollback"):
        denied = await proef.post(route, proef.request(**options, preview=preview))
        assert denied.status_code == 501 and denied.json()["code"] == "capability_unavailable"

    async with sessionmaker()() as db:
        binding = LogicalRole(home_id=proef.home_id, key=role, label="Meetrol", protected=protected)
        db.add(binding)
        await db.flush()
        db.add(DeviceBinding(role_id=binding.id, device_id=stekker))
        await db.commit()
    replay = await proef.post("propose", body, idem)
    assert replay.status_code == 409 and replay.json()["code"] == "policy_denied"
    for route in ("propose", "apply", "rollback"):
        denied = await proef.post(route, proef.request(**options, preview=preview))
        assert denied.status_code == 409 and denied.json()["code"] == "policy_denied"
    bewijs = await proef.evidence(preview["proposal_id"])
    assert bewijs["command_id"] is None and bewijs["rollback_command_id"] is None
    async with sessionmaker()() as db:
        opdrachten = select(func.count()).select_from(CommandRecord).where(CommandRecord.home_id == proef.home_id)
        assert await db.scalar(opdrachten) == 0


@pytest.mark.parametrize(("kind", "provider", "tested", "code"), [
    ("p1_meter", "homewizard", True, "policy_denied"),
    ("kwh_meter", "homewizard", True, "policy_denied"),
    ("zigbee_sensor", "zigbee", True, "policy_denied"),
    ("energy_socket", "test", True, "policy_denied"),
    ("energy_socket", "homewizard", False, "device_not_tested"),
])
async def test_stekkervoorstel_vereist_geteste_homewizard_stekker(proef, stekker, kind, provider, tested, code):
    async with sessionmaker()() as db:
        device = await db.get(Device, stekker)
        device.kind, device.provider = kind, provider
        device.compatibility = "tested" if tested else "untested"
        await db.commit()
    body = proef.request(action="socket.relay.propose", target={"device_id": str(stekker)}, params={"on": True})
    antwoord = await proef.post("propose", body)
    assert antwoord.status_code == 409 and antwoord.json()["code"] == code


@pytest.mark.parametrize(("action", "params", "risk", "claim", "code"), [
    ("zone.delete", {}, "R2", "R3", "action_not_allowed"),
    ("zone.command", {"target_c": 22, "power": True}, "R2", "R0", "validation_error"),
    ("zone.command", {"target_c": 22}, "R1", "R3", "forbidden"),
])
async def test_witte_lijst_schema_en_lokale_risicoklasse(proef, action, params, risk, claim, code):
    answer = await proef.post("apply", proef.request(action=action, params=params, risk=risk, claim=claim))
    assert answer.status_code in (403, 422)
    assert answer.json()["code"] == code
    assert await proef.count() == 0


async def test_zonder_propose_of_passende_goedkeuring_geen_apply(proef):
    missing = await proef.post("apply", proef.request())
    assert missing.status_code == 409 and missing.json()["code"] == "proposal_required"
    preview = await proef.propose()
    no_approval = await proef.post("apply", proef.request(preview=preview, approval=False))
    assert no_approval.status_code == 403
    req = proef.request(preview=preview)
    req["approval"]["preview_hash"] = digest(["ander-voorstel"])
    wrong = await proef.post("apply", proef.sign(req))
    assert wrong.status_code == 409 and wrong.json()["code"] == "approval_mismatch"
    assert await proef.count() == 0


@pytest.mark.parametrize("wijziging", ["etag", "apparaat", "zone", "wachtrij"])
async def test_verouderde_toestand_wordt_geweigerd(proef, wijziging):
    preview = await proef.propose()
    etag = None
    if wijziging == "etag":
        etag = digest(["verouderd"])
    elif wijziging == "apparaat":
        proef.unit.state["SetTem"] = 23
    elif wijziging == "zone":
        async with sessionmaker()() as db:
            zone = await db.get(ClimateZone, proef.zone_id)
            zone.policy = {"override_default_minutes": 45}
            await db.commit()

    r = await proef.post("apply", proef.request(preview=preview, etag=etag))
    if wijziging == "wachtrij":
        assert r.status_code == 202
        proef.unit.state["SetTem"] = 23
        await proef.work()
        assert (await proef.evidence(preview["proposal_id"]))["status"] == "failed"
    else:
        assert r.status_code == 409 and r.json()["code"] == "precondition_failed"
        assert await proef.count() == 0
    assert proef.unit.commands == []


async def test_gree_zonder_correcte_readback_is_mislukt(proef):
    preview = await proef.propose()
    r = await proef.post("apply", proef.request(preview=preview))
    assert r.status_code == 202
    proef.unit.obey = False
    await proef.work()
    result = await proef.evidence(preview["proposal_id"])
    assert result["status"] == "failed" and result["reason_code"] == "readback_mismatch"
    assert result["after"]["target_c"] == result["before"]["target_c"] == 20
    assert len(proef.unit.commands) == 1


async def test_onleesbare_vorige_stand_wordt_geweigerd(proef):
    proef.unit.state["TemUn"] = 1
    preview = await proef.propose()
    assert preview["status"] == "failed" and preview["rollback_recipe"] is None
    r = await proef.post("apply", proef.request(preview=preview))
    assert r.status_code == 409 and await proef.count() == 0
    result = await proef.evidence(preview["proposal_id"])
    assert result["before"] is None and result["reason_code"] == "readback_unavailable"


async def test_schema_voorstel_blijft_concept(proef):
    options = {"action": "zone.schedule.propose", "params": {
        "blocks": [{"days": [0], "start": "06:30", "target_c": 22}],
    }}
    preview = await proef.propose(**options)
    assert preview["concept_only"] and preview["rollback_recipe"] is None
    for route in ("apply", "rollback"):
        answer = await proef.post(route, proef.request(**options, preview=preview))
        assert answer.status_code == 501 and answer.json()["code"] == "capability_unavailable"
    assert await proef.count() == 0
    async with sessionmaker()() as db:
        assert await db.scalar(select(ScheduleBlock.id).where(ScheduleBlock.zone_id == proef.zone_id)) is None


async def test_ongeteste_unit_wordt_geweigerd(proef):
    async with sessionmaker()() as db:
        device = await db.get(Device, proef.device_id)
        device.compatibility = "untested"
        await db.commit()
    denied = await proef.post("apply", proef.request())
    assert denied.status_code == 409 and denied.json()["code"] == "device_not_tested"
    assert await proef.count() == 0


async def test_onherstelbare_zoneactie_wordt_geweigerd(proef):
    options = {"action": "zone.resume", "params": {}}
    preview = await proef.propose(**options)
    assert preview["reason_code"] == "capability_unavailable"
    denied = await proef.post("apply", proef.request(**options, preview=preview))
    assert denied.status_code == 501
    assert await proef.count() == 0


async def test_gedeeltelijke_wijziging_wordt_hersteld(proef, monkeypatch):
    preview = await proef.propose()
    original = proef.unit.datagram_received

    def partial(message, addr):
        count = len(proef.unit.commands)
        original(message, addr)
        if count == 0 and proef.unit.commands:
            proef.unit.state["SetTem"] = 21

    monkeypatch.setattr(proef.unit, "datagram_received", partial)
    r = await proef.post("apply", proef.request(preview=preview))
    assert r.status_code == 202
    await proef.work()
    result = await proef.evidence(preview["proposal_id"])
    assert result["status"] == "failed" and result["after"]["target_c"] == 21
    assert result["rollback_status"] == "confirmed"
    assert result["rollback_after_hash"] == result["before_hash"]
    assert result["recovery_required"] is False
    assert proef.unit.state["SetTem"] == 20
    assert len(proef.unit.commands) == 2


async def test_onderbroken_worker_meldt_onbekend_en_kan_herstellen(proef):
    preview = await proef.propose()
    r = await proef.post("apply", proef.request(preview=preview))
    assert r.status_code == 202
    ident = uuid.UUID(r.json()["command_id"])
    assert (await proef.worker._claim_next({proef.home_id}))[0] == ident
    async with sessionmaker()() as db:
        cmd = await db.get(CommandRecord, ident)
        assert cmd.status == "sending" and cmd.attempts == 1
        cmd.updated_at = utcnow() - timedelta(minutes=3)
        await db.commit()
    proef.unit.state["SetTem"] = 22
    await proef.work()
    proof = await proef.evidence(preview["proposal_id"])
    assert proof["status"] == "failed" and proof["reason_code"] == "readback_unknown_after_restart"
    assert proof["after"] is None and proof["recovery_required"]
    rollback = await proef.post("rollback", proef.request(preview=preview, rollback=proof["rollback_preview"]))
    assert rollback.status_code == 202
    await proef.work()
    restored = await proef.evidence(preview["proposal_id"])
    assert restored["rollback_status"] == "confirmed" and restored["recovery_required"] is False
    assert proef.unit.state["SetTem"] == 20


async def test_migratie_0007_bewaart_bestaande_database_en_stopt_open_connectorcommando():
    async with temp_db("0006") as url:
        house, device, zone, user, role, binding, old_cmd = (uuid.uuid4() for _ in range(7))
        await sql(url, """
            INSERT INTO homes (id,name,timezone,currency,active_from)
            VALUES (:id,'Proefwoning','UTC','EUR',now())
        """, {"id": house})
        await sql(url, """
            INSERT INTO devices (id,home_id,kind,name,stable_identity,provider,capabilities,
                                 compatibility,lifecycle,online,state,discovered_at)
            VALUES (:id,:home,'light_group','Oude groep','proefgroep','hue',
                    '[]','tested','active',true,'{"on": true}',now())
        """, {"id": device, "home": house})
        await sql(url, """
            INSERT INTO climate_zones (id,home_id,name,policy,control,desired_mode,last_commanded,
                                       start_times,adaptive_enabled)
            VALUES (:id,:home,'Oude zone','{}','schedule','off','{}','[]',false)
        """, {"id": zone, "home": house})
        await sql(url, """
            INSERT INTO users (id,username,password_hash,role,disabled,created_at)
            VALUES (:id,'proefbeheer','!fixture','admin',false,now())
        """, {"id": user})
        await sql(url, """
            INSERT INTO logical_roles (id,home_id,key,label,protected)
            VALUES (:id,:home,'licht','Groepslicht',false)
        """, {"id": role, "home": house})
        await sql(url, """
            INSERT INTO device_bindings (id,role_id,device_id,effective_from)
            VALUES (:id,:role,:device,now())
        """, {"id": binding, "role": role, "device": device})
        await sql(url, """
            INSERT INTO commands (id,home_id,device_id,action,params,source,actor,status,detail,
                                  attempts,created_at,updated_at)
            VALUES (:id,:home,:device,'set_group','{"on": true}','user','proefbeheer','queued','',0,now(),now())
        """, {"id": old_cmd, "home": house, "device": device})

        async def snapshot():
            return (
                await sql(url, "SELECT to_jsonb(t) FROM homes t ORDER BY id"),
                await sql(url, "SELECT to_jsonb(t) - 'connector_version' FROM devices t ORDER BY id"),
                await sql(url, "SELECT to_jsonb(t) - 'connector_version' FROM climate_zones t ORDER BY id"),
                await sql(url, "SELECT to_jsonb(t) FROM commands t ORDER BY id"),
                await sql(url, "SELECT to_jsonb(t) FROM users t ORDER BY id"),
                await sql(url, "SELECT to_jsonb(t) FROM device_bindings t ORDER BY id"),
            )

        original = await snapshot()
        config = bk.alembic_config(url)
        await asyncio.to_thread(command.upgrade, config, "0007")
        assert await snapshot() == original
        await sql(url, "UPDATE devices SET state = '{\"on\": false}' WHERE id=:id", {"id": device})
        assert await sql(url, "SELECT connector_version FROM devices WHERE id=:id", {"id": device}) == [(2,)]
        await sql(url, "UPDATE devices SET state = '{\"on\": true}' WHERE id=:id", {"id": device})
        await sql(url, "UPDATE climate_zones SET control = 'manual' WHERE id=:id", {"id": zone})
        assert await sql(url, "SELECT connector_version FROM climate_zones WHERE id=:id", {"id": zone}) == [(2,)]
        await sql(url, "UPDATE climate_zones SET control = 'schedule' WHERE id=:id", {"id": zone})
        queued = uuid.uuid4()
        await sql(url, """
            INSERT INTO commands (id,home_id,device_id,action,params,source,actor,status,detail,
                                  attempts,created_at,updated_at)
            VALUES (:id,:home,:device,'set_hvac','{"target_c": 22}','connector','Hub','queued','',0,now(),now())
        """, {"id": queued, "home": house, "device": device})
        from shc_api.cli import MigrationError
        with pytest.raises(MigrationError, match="connectorherstelbewijs"):
            await asyncio.to_thread(command.downgrade, config, "0006")
        assert await sql(url, "SELECT status FROM commands WHERE id=:id", {"id": queued}) == [("queued",)]
        await sql(url, "DELETE FROM commands WHERE id=:id", {"id": queued})
        await asyncio.to_thread(command.downgrade, config, "0006")
        assert await snapshot() == original
        await asyncio.to_thread(command.upgrade, config, "0007")
        assert await snapshot() == original


@pytest.mark.parametrize(("kind", "action", "field", "params"), [
    ("light_group", "light.group.set", "group_id", {"on": True}),
    ("scene", "light.scene.recall", "scene_id", {}),
])
async def test_licht_zonder_betrouwbaar_herstelpad_wordt_niet_bediend(proef, kind, action, field, params):
    async with sessionmaker()() as db:
        device = await db.get(Device, proef.device_id)
        device.kind, device.provider = kind, "hue"
        await db.commit()
    options = {"action": action, "target": {field: str(proef.device_id)}, "params": params, "risk": "R1"}
    preview = await proef.propose(**options)
    assert preview["status"] == "unavailable" and preview["rollback_recipe"] is None
    assert (await proef.post("apply", proef.request(preview=preview, **options))).status_code == 501
    assert await proef.count() == 0


async def test_geen_herstel_van_een_geweigerde_opdracht(proef):
    preview = await proef.propose()
    accepted = await proef.post("apply", proef.request(preview=preview))
    assert accepted.status_code == 202
    proef.unit.state["SetTem"] = 22
    await proef.work()
    result = await proef.evidence(preview["proposal_id"])
    assert result["status"] == "failed"
    assert result["freshness"] == "unknown"
    assert result["recovery_required"] is False
    rollback = await proef.post("rollback", proef.request(preview=preview))
    assert rollback.status_code == 409
    assert proef.unit.commands == []
