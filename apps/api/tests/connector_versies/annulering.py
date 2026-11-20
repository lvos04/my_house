"""Dezelfde schema-annulering op echte previews van beide historische 0007-versies."""

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from herstelbewijs import route
from overdracht import herstel_archief, zonder_quiet
from versie import andere_versie

from shc_api import cli
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import ClimateZone, utcnow
from tests.test_connector_routes import proef as proef
from tests.test_inbedrijfstelling_migratie import sql
from tests.test_ws_backup_format import temp_db


async def test_schema_annulering_met_oorspronkelijke_preview(proef, tmp_path, monkeypatch):
    url = get_settings().database_url
    assert cli.current_revision(url) == "0007"
    with monkeypatch.context() as patch:
        patch.setattr(proef.unit, "_reply", zonder_quiet(proef.unit))
        oud = await proef.propose()
        assert oud["status"] == "proposed"
        verzoek = proef.request(preview=oud)
        verzoek["approval"]["request_id"] = verzoek["proposal"]["idempotency_key"]
        antwoord = await proef.post("apply", proef.sign(verzoek))
        assert antwoord.status_code == 202 and not proef.unit.commands
    assert "Quiet" not in oud["before"].get("controls", {})
    opdracht = {"id": uuid.UUID(antwoord.json()["command_id"])}
    voorstel = {"id": uuid.UUID(oud["proposal_id"])}
    rij = "SELECT status, attempts FROM commands WHERE id=:id"
    record = "SELECT before, preview_hash, command_id FROM connector_changes WHERE id=:id"
    assert await sql(url, rij, opdracht) == [("queued", 0)]
    assert await sql(url, record, voorstel) == [(oud["before"], oud["preview_hash"], opdracht["id"])]
    archief = await asyncio.to_thread(cli.pre_migration_dump, url, "0007", tmp_path / "bron")
    async with temp_db(None) as kopie:
        await herstel_archief(archief, kopie)
        opties = {"actie": "migreer", "map": str(tmp_path / "eerste-upgrade")}
        assert await andere_versie(opties, versie="schema0008", url=kopie) == {
            "schema": "0008", "archief": True, "geweigerd": False,
        }
        assert await sql(kopie, record, voorstel) == [(oud["before"], oud["preview_hash"], opdracht["id"])]
        assert await sql(kopie, rij, opdracht) == [("queued", 0)]
        # De gedocumenteerde migratie uit de basiscommit annuleert en archiveert het lopende werk.
        basis = Path(os.environ["PROEF_VERSIES"]) / "basis"
        env = dict(os.environ, SHC_DATABASE_URL=kopie,
                   PYTHONPATH=os.pathsep.join(str(basis / d) for d in ("apps/api/src", "integrations/src", "apps/api")))
        daling = """
from pathlib import Path
import sys
from datetime import timedelta
from urllib.parse import urlsplit
from alembic import command
from shc_api import backup, cli
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import ClimateZone, utcnow
url = get_settings().database_url
assert Path(cli.__file__).resolve().is_relative_to(Path(sys.argv[1]))
assert urlsplit(url).hostname == 'localhost' and urlsplit(url).path.startswith('/shc_tmp_')
archief = cli.pre_migration_dump(url, '0008', Path(sys.argv[2]))
cli.verify_migration_dump(archief)
command.downgrade(backup.alembic_config(url), '0006')
assert cli.current_revision(url) == '0006'
"""
        await asyncio.to_thread(subprocess.run, [sys.executable, "-B", "-c", daling, str(basis), str(tmp_path / "basis")],
                                cwd=basis / "apps/api", env=env, check=True, capture_output=True)
        assert await sql(kopie, rij, opdracht) == [("cancelled", 0)]
        feiten = await sql(kopie, """
            SELECT value->'v'->'before', value->'v'->>'preview_hash', value->'v'->>'command_id'
            FROM settings WHERE key=:key
        """, {"key": "connector_history:" + oud["proposal_id"]})
        assert feiten == [(oud["before"], oud["preview_hash"], str(opdracht["id"]))]
        assert await sql(kopie, "SELECT count(*) FROM idempotency_keys WHERE key=:id", voorstel) == [(0,)]
        # Na terugkeer naar de oude 7b-applicatie levert een echte simulatormeting de latere bedienstand.
        start7b = await andere_versie({"actie": "migreer", "map": str(tmp_path / "oude-start")},
                                      versie="schema0007b", url=kopie)
        assert start7b == {"schema": "0007", "archief": True, "geweigerd": False}
        proef.key_id = str(uuid.uuid4())
        later = await andere_versie({
            "actie": "route", "methode": "POST", "route": "propose", "verzoek": proef.request(),
            "koppen": {**proef.admin.h(), "Idempotency-Key": str(uuid.uuid4())},
            "sessie": dict(proef.admin.c.cookies),
        }, versie="schema0007b", url=kopie)
        assert later["http"] == 200 and later["inhoud"]["before"]["controls"]["Quiet"] == 0
        dump = await andere_versie({"actie": "dump", "map": str(tmp_path / "dump"),
                                    "voorstel": oud["proposal_id"]}, url=kopie)
        start = await andere_versie({"actie": "migreer", "map": str(tmp_path / "hernemen")}, url=kopie)
        print(f"HISTORISCHE_ANNULERING oorspronkelijke_velden={len(oud['before'])} "
              f"preview_gelijk=True opdracht_id_gelijk=True status=cancelled claims=0 rechten=0 "
              f"later_Quiet=0 dump_geweigerd={dump['geweigerd']} start_geweigerd={start['geweigerd']} "
              f"schema_na_start={start['schema']} verzendingen={len(proef.unit.commands)}")
        assert not proef.unit.commands and proef.unit.state["SetTem"] == 20
        assert not dump["geweigerd"] and not start["geweigerd"]
        assert await sql(kopie, record, voorstel) == [(oud["before"], oud["preview_hash"], opdracht["id"])]


async def test_dezelfde_unit_in_andere_zone(proef, tmp_path, monkeypatch):
    """Een oude meting met stille stand moet de unit ook na verhuizing beschermen."""
    url = get_settings().database_url
    assert cli.current_revision(url) == "0007"
    async with sessionmaker()() as db:
        nieuw = ClimateZone(home_id=proef.home_id, name="Tweede proefzone")
        db.add(nieuw)
        await db.commit()
        nieuw_id = nieuw.id
    eerste = await proef.propose()
    assert eerste["before"]["controls"]["Quiet"] == 0 and await proef.count() == 0
    for zone_id, unit_id in ((proef.zone_id, None), (nieuw_id, str(proef.device_id))):
        r = await proef.admin.c.patch(f"/api/v1/zones/{zone_id}", headers=proef.admin.h(),
                                      json={"unit_device_id": unit_id})
        assert r.status_code == 200
    handmatig = await proef.admin.post(f"/zones/{nieuw_id}/command", {"target_c": 20, "override_minutes": 60})
    assert handmatig.status_code == 202
    await proef.work()
    assert len(proef.unit.commands) == 1 and proef.unit.state["Quiet"] == 0 and proef.unit.state["SetTem"] == 20
    aantal_voor = len(proef.unit.commands)
    assert (await andere_versie({"actie": "migreer", "map": str(tmp_path)})) == {
        "schema": "0009", "geweigerd": False, "archief": True,
    }
    oud_id = {"id": uuid.UUID(eerste["proposal_id"])}
    assert await sql(url, "SELECT before->'controls'->'Quiet' FROM connector_changes WHERE id=:id", oud_id) == [(0,)]
    doel = {"zone_id": str(nieuw_id)}
    with monkeypatch.context() as patch:
        patch.setattr(proef.unit, "_reply", zonder_quiet(proef.unit))
        proef.key_id = str(uuid.uuid4())
        plan = (await route(proef, "propose", proef.request(target=doel)))["inhoud"]
        antwoord = await route(proef, "apply", proef.request(target=doel, preview=plan))
        assert antwoord["http"] == 409 and await proef.count() == aantal_voor
        assert len(proef.unit.commands) == aantal_voor and proef.unit.state["Quiet"] == 0
    proef.key_id = str(uuid.uuid4())
    vers = (await route(proef, "propose", proef.request(target=doel)))["inhoud"]
    assert vers["before"]["controls"]["Quiet"] == 0
    assert (await route(proef, "apply", proef.request(target=doel, preview=vers)))["http"] == 202
    assert await proef.count() == aantal_voor + 1


async def test_toekomstige_reservering_na_softwarewissel(proef, tmp_path):
    from tests.test_connector_herstel import _apply, _queue_rollback

    url = get_settings().database_url
    plan, _ = await _apply(proef)
    herstel = await _queue_rollback(proef, plan)
    sleutel = {"id": uuid.UUID(herstel)}
    later = utcnow() + timedelta(hours=1)
    await sql(url, "UPDATE commands SET updated_at=:tijd WHERE id=:id", {**sleutel, "tijd": later})
    start = await andere_versie({"actie": "migreer", "map": str(tmp_path)})
    assert not start["geweigerd"] and start["archief"]
    bewijs = (await route(proef, "evidence/" + herstel))["inhoud"]
    assert bewijs["rollback_command_id"] == herstel and bewijs["recovery_required"]
    assert bewijs["rollback_reason_code"] == "versiewissel" and bewijs["rollback_preview"] is None
    werk = {"actie": "werk", "woning": str(proef.home_id), "controleer_start": True}
    for versie in ("voorgaand", "basis", None):
        await andere_versie(werk, versie=versie)
        assert await sql(url, "SELECT status, attempts, detail FROM commands WHERE id=:id", sleutel) == [
            ("cancelled", 0, "versiewissel"),
        ]
    assert len(proef.unit.commands) == 1 and proef.unit.state["SetTem"] == 22
    meldingen = await proef.admin.get(f"/homes/{proef.home_id}/alerts")
    assert meldingen.status_code == 200
    assert any("handmatige controle nodig" in m["title"].lower() and herstel in m["detail"] for m in meldingen.json())


async def test_reeds_actieve_worker_mag_nieuwe_opdracht_niet_claimen(proef):
    plan = await proef.propose()
    antwoord = await proef.post("apply", proef.request(preview=plan))
    assert antwoord.status_code == 202
    ident = {"id": uuid.UUID(antwoord.json()["command_id"])}
    rij = "SELECT status, attempts FROM commands WHERE id=:id"
    # Deze vorige worker heeft zijn startcontrole al vóór de softwarewissel gedaan.
    werk = await andere_versie({"actie": "werk", "woning": str(proef.home_id)}, versie="basis")
    assert werk["geblokkeerd"] and not proef.unit.commands
    assert await sql(get_settings().database_url, rij, ident) == [("queued", 0)]
    await proef.work()
    assert await sql(get_settings().database_url, rij, ident) == [("confirmed", 1)]
    assert len(proef.unit.commands) == 1 and proef.unit.state["SetTem"] == 22
