"""Oorspronkelijke metingen, reserveringen en ongeclaimde historie over een softwarewissel."""

import asyncio
import subprocess
import uuid
from datetime import timedelta

import pytest
from alembic import command
from herstelbewijs import route
from versie import andere_versie

from shc_api import backup, cli
from shc_api.config import get_settings
from shc_api.models import utcnow
from tests.test_connector_routes import proef as proef
from tests.test_inbedrijfstelling_migratie import sql
from tests.test_ws_backup_format import PW, temp_db


def zonder_quiet(unit):
    antwoord = unit._reply

    def lees(bericht, *args, **kwargs):
        if bericht["t"] == "dat" and "Quiet" in bericht["cols"]:
            paren = [(veld, waarde) for veld, waarde in zip(bericht["cols"], bericht["dat"], strict=True)
                     if veld != "Quiet"]
            bericht = {**bericht, "cols": [p[0] for p in paren], "dat": [p[1] for p in paren]}
        if bericht["t"] == "res":
            unit.state["Quiet"] = 1
        return antwoord(bericht, *args, **kwargs)

    return lees


async def wijzig_buitengroep(proef):
    wijziging = await proef.admin.c.patch(f"/api/v1/zones/{proef.zone_id}", headers=proef.admin.h(),
                                         json={"outdoor_group": "Proefgroep"})
    assert wijziging.status_code == 200


async def controleer_groepswissel(proef, tmp_path, oud):
    url = get_settings().database_url
    assert cli.current_revision(url) == "0007"
    ident = {"id": proef.zone_id}
    unit = "SELECT unit_device_id, connector_version FROM climate_zones WHERE id=:id"
    voor = (await sql(url, unit, ident))[0]
    await wijzig_buitengroep(proef)
    na = (await sql(url, unit, ident))[0]
    assert voor[0] == na[0] == proef.device_id and na[1] > voor[1]
    start = await andere_versie({"actie": "migreer", "map": str(tmp_path)})
    assert start["schema"] == "0008" and not start["geweigerd"]
    assert await sql(url, "SELECT before FROM connector_changes WHERE id=:id",
                     {"id": uuid.UUID(oud["proposal_id"])}) == [(oud["before"],)]
    proef.key_id = str(uuid.uuid4())
    voorstel = await route(proef, "propose", proef.request())
    plan = voorstel["inhoud"]
    assert voorstel["http"] == 200 and plan["status"] == "failed"
    verzoek = proef.request(preview=plan)
    # De eerste client gebruikte een losse request_id. Geef de huidige route de bijpassende goedkeuring.
    verzoek["approval"]["request_id"] = proef.key_id
    antwoord = await route(proef, "apply", proef.sign(verzoek))
    assert antwoord["http"] == 409 and antwoord["inhoud"]["code"] == "readback_unavailable"
    assert await proef.count() == 0 and not proef.unit.commands and proef.unit.state["Quiet"] == 0


async def test_oude_meting_na_wijziging_buitengroep(proef, tmp_path, monkeypatch):
    oud = await proef.propose()
    assert oud["status"] == "proposed" and oud["before"]["controls"]["Quiet"] == 0
    monkeypatch.setattr(proef.unit, "_reply", zonder_quiet(proef.unit))
    await controleer_groepswissel(proef, tmp_path, oud)


async def test_oude_drieveldmeting_na_groepswissel(proef, tmp_path):
    oud = await proef.propose()
    assert oud["status"] == "proposed" and set(oud["before"]) == {"power", "mode", "target_c"}
    await controleer_groepswissel(proef, tmp_path, oud)


async def test_meetidentiteit_bij_nieuwe_groep(proef, tmp_path, monkeypatch):
    url = get_settings().database_url
    oud = await proef.propose()
    await wijzig_buitengroep(proef)
    proef.key_id = str(uuid.uuid4())
    nieuw = await proef.propose()
    assert oud["before"] == nieuw["before"] and oud["resource_etag"] != nieuw["resource_etag"]
    for voorstel, groep in ((oud, None), (nieuw, "Proefgroep")):
        context, = (await sql(url, "SELECT value->'v'->:preview FROM settings WHERE key=:key",
                              {"key": "connector_measurement:" + voorstel["proposal_id"],
                               "preview": voorstel["preview_hash"]}))[0]
        assert context["unit_id"] == str(proef.device_id) and context["zone_id"] == str(proef.zone_id)
        assert context["outdoor_group"] == groep
    await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
    monkeypatch.setattr(proef.unit, "_reply", zonder_quiet(proef.unit))
    antwoord = await proef.post("apply", proef.request(preview=nieuw))
    assert antwoord.status_code == 409 and not proef.unit.commands and await proef.count() == 0
    await sql(url, "DELETE FROM settings WHERE key=:key", {"key": "connector_measurement:" + oud["proposal_id"]})
    with pytest.raises(cli.MigrationError, match="connectorherstelbewijs"):
        await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
    with pytest.raises(backup.BackupError, match="connectorherstelbewijs"):
        await backup.run_db_dump(PW, auto=False, url=url, backup_dir=tmp_path)


async def herstel_archief(pad, doel):
    sql_pad = pad.with_suffix(".hersteld.sql")
    await asyncio.to_thread(cli.verify_migration_dump, pad, sql_pad)
    await asyncio.to_thread(subprocess.run, ["psql", "-Xq", "--single-transaction", "-v", "ON_ERROR_STOP=1",
                            "-d", cli._libpq(doel)[0], "-f", str(sql_pad)], capture_output=True, check=True)


async def voltooide_rollback(proef, url, herstel, versie=None):
    werk = await andere_versie({"actie": "werk", "woning": str(proef.home_id), "controleer_start": True},
                              versie=versie, url=url)
    assert not werk["geblokkeerd"]
    bewijs = await sql(url, """
        SELECT c.rollback_status, c.rollback_after = c.before, c.rollback_command_id::text, cmd.attempts
        FROM connector_changes c JOIN commands cmd ON cmd.id=c.rollback_command_id WHERE cmd.id=:id
    """, {"id": uuid.UUID(herstel)})
    assert bewijs == [("confirmed", True, herstel, 1)]
    assert proef.unit.state["SetTem"] == 20 and len(proef.unit.commands) == 2


async def oude_rollback(proef):
    from tests.test_connector_herstel import _apply, _queue_rollback

    assert cli.current_revision() == "0008"
    voorstel, _ = await _apply(proef)
    herstel = await _queue_rollback(proef, voorstel)
    assert proef.unit.state["SetTem"] == 22
    return voorstel, herstel


@pytest.mark.parametrize("terugval", [False, True], ids=["overdracht", "terugval"])
async def test_zelfde_schema_vooraf_archief(proef, tmp_path, terugval):
    url = get_settings().database_url
    voorstel, herstel = await oude_rollback(proef)
    ident = {"id": uuid.UUID(herstel)}
    rij = "SELECT updated_at, status, attempts FROM commands WHERE id=:id"
    voor = (await sql(url, rij, ident))[0]
    assert voor[1:] == ("queued", 0) and voor[0].year < 9999
    werk = {"actie": "werk", "woning": str(proef.home_id), "controleer_start": True}
    assert (await andere_versie(werk))["geblokkeerd"]
    assert await sql(url, rij, ident) == [voor]
    start = {"actie": "migreer", "map": str(tmp_path)}
    assert await andere_versie(start) == {"schema": "0008", "geweigerd": False, "archief": True}
    assert not (await andere_versie(start))["archief"]
    archief, = tmp_path.glob("*.sql.gz")
    verwachting = [(voor[0], "connector:voorstel:2")]
    bewaard = await sql(url, """
        SELECT (r->>'claim_after')::timestamptz, r->>'worker' FROM idempotency_keys k,
               LATERAL (SELECT k.response_body->'reservations'->:opdracht AS r) s
        WHERE k.scope='connector:voorstel:2' AND k.key=:id
    """, {"id": uuid.UUID(voorstel["proposal_id"]), "opdracht": herstel})
    assert bewaard == verwachting
    na = await sql(url, rij, ident)
    assert na[0][1:] == voor[1:]
    await andere_versie(werk, versie="voorgaand")
    assert await sql(url, rij, ident) == na and len(proef.unit.commands) == 1
    async with temp_db(None) as kopie:
        await herstel_archief(archief, kopie)
        assert await sql(kopie, rij, ident) == [voor]
        if terugval:
            await voltooide_rollback(proef, kopie, herstel, "voorgaand")
        else:
            await voltooide_rollback(proef, url, herstel)


async def test_zelfde_schema_rollback_in_voorgaande_software(proef):
    _, herstel = await oude_rollback(proef)
    await voltooide_rollback(proef, get_settings().database_url, herstel, "voorgaand")


async def test_gedocumenteerde_export_voor_softwareterugval(proef, tmp_path):
    url = get_settings().database_url
    _, herstel = await oude_rollback(proef)
    rij = "SELECT status, attempts, updated_at FROM commands WHERE id=:id"
    ident = {"id": uuid.UUID(herstel)}
    voor = await sql(url, rij, ident)
    archief = await asyncio.to_thread(cli.pre_migration_dump, url, "0008", tmp_path / "vooraf")
    assert not (await andere_versie({"actie": "migreer", "map": str(tmp_path / "nieuw")}))["geweigerd"]
    async with temp_db(None) as kopie:
        await herstel_archief(archief, kopie)
        assert await sql(kopie, rij, ident) == voor
        await voltooide_rollback(proef, kopie, herstel, "voorgaand")


async def test_compatibele_worker_behoudt_oorspronkelijke_rollback(proef, tmp_path):
    from tests.test_connector_herstel import _apply

    url = get_settings().database_url
    plan, _ = await _apply(proef)
    sleutel = {"key": "connector_measurement:" + plan["proposal_id"]}
    context = "SELECT value FROM settings WHERE key=:key"
    voor = await sql(url, context, sleutel)
    assert len(voor) == 1
    assert not (await andere_versie({"actie": "migreer", "map": str(tmp_path)}, versie="voor_herstel"))["geweigerd"]
    bewijs = await proef.evidence(plan["proposal_id"])
    verzoek = proef.request(preview=plan, rollback=bewijs["rollback_preview"])
    antwoord = await andere_versie({
        "actie": "route", "methode": "POST", "route": "rollback", "verzoek": verzoek,
        "koppen": {**proef.admin.h(), "Idempotency-Key": str(uuid.uuid4())},
        "sessie": dict(proef.admin.c.cookies),
    }, versie="voor_herstel")
    assert antwoord["http"] == 202
    herstel = antwoord["inhoud"]["command_id"]
    await voltooide_rollback(proef, url, herstel, "voor_herstel")
    assert (await proef.evidence(herstel))["rollback_command_id"] == herstel
    await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
    assert await sql(url, context, sleutel) == voor


async def test_reservering_bij_geweigerde_export_en_later_claimtijdstip(proef, tmp_path, monkeypatch):
    url = get_settings().database_url
    voorstel, herstel = await oude_rollback(proef)
    later = utcnow() + timedelta(hours=1)
    opdracht = {"id": uuid.UUID(herstel)}
    await sql(url, "UPDATE commands SET updated_at=:later WHERE id=:id", {**opdracht, "later": later})
    rij = "SELECT updated_at, status, attempts FROM commands WHERE id=:id"
    sleutel = {"id": uuid.UUID(voorstel["proposal_id"])}
    recht = "SELECT to_jsonb(k) FROM idempotency_keys k WHERE key=:id"
    voor = await sql(url, recht, sleutel)
    verify = cli.verify_migration_dump

    def beschadig(dump):
        dump.with_name(dump.name + ".sha256").write_text("ongeldige controlesom")
        verify(dump)

    with monkeypatch.context() as m:
        m.setattr(cli, "verify_migration_dump", beschadig)
        with pytest.raises(cli.MigrationError, match="SHA-256"):
            await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
    assert not list(tmp_path.glob("*.sql.gz*"))
    assert await sql(url, recht, sleutel) == voor
    assert await sql(url, rij, opdracht) == [(later, "queued", 0)]
    terug = await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
    await asyncio.to_thread(verify, terug)
    await proef.work()
    await andere_versie({"actie": "werk", "woning": str(proef.home_id), "controleer_start": True}, versie="voorgaand")
    assert await sql(url, rij, opdracht) == [(later, "queued", 0)]
    assert proef.unit.state["SetTem"] == 22 and len(proef.unit.commands) == 1


async def test_onuitgevoerde_historie_na_nieuw_bedienveld(proef, tmp_path, monkeypatch):
    url = get_settings().database_url
    with monkeypatch.context() as m:
        m.setattr(proef.unit, "_reply", zonder_quiet(proef.unit))
        toen = await proef.propose()
        assert "Quiet" not in toen["before"]["controls"] and toen["status"] == "proposed"
        antwoord = await proef.post("apply", proef.request(preview=toen))
        assert antwoord.status_code == 202
    ident = {"id": uuid.UUID(antwoord.json()["command_id"])}
    await asyncio.to_thread(command.downgrade, backup.alembic_config(url), "0006")
    migratie = await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
    await asyncio.to_thread(cli.verify_migration_dump, migratie)
    assert await sql(url, "SELECT status, attempts FROM commands WHERE id=:id", ident) == [("cancelled", 0)]
    sleutel = {"id": uuid.UUID(toen["proposal_id"])}
    assert await sql(url, "SELECT count(*) FROM idempotency_keys WHERE key=:id", sleutel) == [(0,)]
    proef.key_id = str(uuid.uuid4())
    later = await proef.propose()
    assert later["before"]["controls"]["Quiet"] == 0

    for versie in ("voorgaand", None):
        mapnaam = tmp_path / (versie or "nieuw")
        dump = await andere_versie({"actie": "dump", "map": str(mapnaam), "voorstel": toen["proposal_id"]}, versie=versie)
        assert not dump["geweigerd"]
        rij, = dump["rijen"]
        assert rij[0] == "cancelled" and rij[3:] == [str(ident["id"]), toen["before"], 0]
        assert not (await andere_versie({"actie": "migreer", "map": str(mapnaam)}, versie=versie))["geweigerd"]
    await proef.work()
    assert not proef.unit.commands and proef.unit.state["SetTem"] == 20

    # Zowel broncontrole als herstart moeten een achtergebleven lokaal recht herkennen.
    await sql(url, """
        INSERT INTO idempotency_keys (key, scope, request_hash)
        SELECT id, 'connector:voorstel:2', decode(substr(preview_hash, 8), 'hex') FROM connector_changes WHERE id=:id
    """, sleutel)
    with pytest.raises(backup.BackupError, match="uitvoerrecht"):
        await backup.run_db_dump(PW, auto=False, url=url, backup_dir=tmp_path / "met-recht")
    with pytest.raises(cli.MigrationError, match="connectorherstelbewijs"):
        await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
    await sql(url, "DELETE FROM idempotency_keys WHERE key=:id", sleutel)
    await sql(url, "UPDATE commands SET attempts=1 WHERE id=:id", ident)
    with pytest.raises(backup.BackupError, match="connectorherstelbewijs"):
        await backup.run_db_dump(PW, auto=False, url=url, backup_dir=tmp_path / "met-claim")
    await sql(url, "UPDATE commands SET attempts=0 WHERE id=:id", ident)

    # Een beschadigde preview blijft ook voor deze onuitgevoerde historie een harde fout.
    await sql(url, "UPDATE connector_changes SET before=before #- '{controls,WdSpd}' WHERE id=:id", sleutel)
    assert (await andere_versie({"actie": "dump", "map": str(tmp_path / "beschadigd"),
                                 "voorstel": toen["proposal_id"]}))["geweigerd"]
    assert (await andere_versie({"actie": "migreer", "map": str(tmp_path)}))["geweigerd"]
