"""Oorspronkelijke metingen, reserveringen en ongeclaimde historie over een softwarewissel."""

import asyncio
import subprocess
import uuid

import pytest
from alembic import command
from herstelbewijs import route
from versie import andere_versie

from shc_api import backup, cli
from shc_api.config import get_settings
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
    assert start["schema"] == "0009" and not start["geweigerd"]
    assert await sql(url, "SELECT before FROM connector_changes WHERE id=:id",
                     {"id": uuid.UUID(oud["proposal_id"])}) == [(oud["before"],)]
    oud_verzoek = proef.request(preview=oud)
    oud_verzoek["approval"]["request_id"] = proef.key_id
    assert (await route(proef, "apply", proef.sign(oud_verzoek)))["http"] == 409
    assert await proef.count() == 0 and not proef.unit.commands
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


async def test_oude_drieveldmeting_na_groepswissel(proef, tmp_path, monkeypatch):
    oud = await proef.propose()
    assert oud["status"] == "proposed" and set(oud["before"]) == {"power", "mode", "target_c"}
    with monkeypatch.context() as patch:
        patch.setattr(proef.unit, "_reply", zonder_quiet(proef.unit))
        await controleer_groepswissel(proef, tmp_path, oud)
    proef.key_id = str(uuid.uuid4())
    nieuw = (await route(proef, "propose", proef.request()))["inhoud"]
    assert nieuw["status"] == "proposed" and nieuw["before"]["controls"]["Quiet"] == 0
    verzoek = proef.request(preview=nieuw)
    verzoek["approval"]["request_id"] = proef.key_id
    assert (await route(proef, "apply", proef.sign(verzoek)))["http"] == 202
    assert await proef.count() == 1 and not proef.unit.commands


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


@pytest.mark.parametrize("archiefsoort", ["automatisch", "handmatig", "opslagkopie"])
async def test_versiewissel_annuleert_ook_op_terugvalkopie(proef, tmp_path, archiefsoort):
    url = get_settings().database_url
    voorstel, herstel = await oude_rollback(proef)
    ident = {"id": uuid.UUID(herstel)}
    rij = "SELECT status, attempts, detail FROM commands WHERE id=:id"
    assert (await sql(url, rij, ident))[0][:2] == ("queued", 0)
    if archiefsoort != "automatisch":
        archief = await asyncio.to_thread(cli.pre_migration_dump, url, "0008", tmp_path / "handmatig")
    start = {"actie": "migreer", "map": str(tmp_path)}
    assert (await andere_versie(start)) == {"schema": "0009", "geweigerd": False, "archief": True}
    assert not (await andere_versie(start))["archief"]
    if archiefsoort == "automatisch":
        archief, = tmp_path.glob("*.sql.gz")
    verwacht = [("cancelled", 0, "versiewissel")]
    assert await sql(url, rij, ident) == verwacht
    async with temp_db(None) as kopie:
        pad = tmp_path / "gecontroleerd.sql"
        if archiefsoort == "opslagkopie":
            # De oude software schrijft de oorspronkelijke rijen zonder annulering.
            await asyncio.to_thread(cli.verify_migration_dump, archief, pad)
        else:
            assert (await andere_versie({"actie": "controleer_sql", "archief": str(archief),
                                         "uitvoer": str(pad)}))["gecontroleerd"]
        await asyncio.to_thread(subprocess.run, ["psql", "-Xq", "--single-transaction", "-v", "ON_ERROR_STOP=1",
                                "-d", cli._libpq(kopie)[0], "-f", str(pad)], capture_output=True, check=True)
        if archiefsoort == "opslagkopie":
            assert (await sql(kopie, rij, ident))[0][:2] == ("queued", 0)
            assert (await andere_versie({"actie": "sluit_kopie"}, url=kopie)) == {"schema": "0008"}
        werk = {"actie": "werk", "woning": str(proef.home_id), "controleer_start": True}
        await andere_versie(werk, versie="voorgaand", url=kopie)
        assert await sql(kopie, rij, ident) == verwacht
        assert await sql(kopie, "SELECT before, preview_hash FROM connector_changes WHERE id=:id",
                         {"id": uuid.UUID(voorstel["proposal_id"])}) == [(voorstel["before"], voorstel["preview_hash"])]
        assert await sql(kopie, "SELECT count(*) FROM alerts WHERE key=:key",
                         {"key": "connector:rollback:" + herstel}) == [(1,)]
        assert not (await andere_versie(start, url=kopie))["geweigerd"]
        assert await sql(kopie, rij, ident) == verwacht
    assert len(proef.unit.commands) == 1 and proef.unit.state["SetTem"] == 22


async def test_zelfde_schema_rollback_in_voorgaande_software(proef):
    _, herstel = await oude_rollback(proef)
    await voltooide_rollback(proef, get_settings().database_url, herstel, "voorgaand")


async def test_alleen_lezen_terugval_en_opnieuw_vooruit(proef, tmp_path):
    from tests.test_connector_herstel import _apply, _queue_rollback

    plan, _ = await _apply(proef)
    herstel = await _queue_rollback(proef, plan)
    opties = {"actie": "migreer", "map": str(tmp_path)}
    assert (await andere_versie(opties, versie="basis"))["geweigerd"]
    werk = {"actie": "werk", "woning": str(proef.home_id), "controleer_start": True}
    assert (await andere_versie(werk, versie="basis"))["geblokkeerd"]
    assert not (await andere_versie(opties))["geweigerd"]
    assert not (await andere_versie(werk))["geblokkeerd"]
    assert (await proef.evidence(herstel))["rollback_reason_code"] == "versiewissel"
    assert await sql(get_settings().database_url, "SELECT status, attempts FROM commands WHERE id=:id",
                     {"id": uuid.UUID(herstel)}) == [("cancelled", 0)]
    assert len(proef.unit.commands) == 1 and proef.unit.state["SetTem"] == 22


async def test_softwarewissel_op_hetzelfde_schema_en_opnieuw_vooruit(proef, tmp_path, monkeypatch):
    from shc_api import connector_changes, connector_transition
    from tests.test_connector_herstel import _apply, _queue_rollback

    url = get_settings().database_url
    verify = cli.verify_migration_dump
    versie_a = connector_changes.LOCAL_PROPOSAL
    for huidige, volgende in ((versie_a, versie_a + "-volgend"), (versie_a + "-volgend", versie_a)):
        monkeypatch.setattr(connector_changes, "LOCAL_PROPOSAL", huidige)
        monkeypatch.setattr(connector_transition, "LOCAL_PROPOSAL", huidige)
        proef.key_id = str(uuid.uuid4())
        plan, _ = await _apply(proef)
        herstel = await _queue_rollback(proef, plan)
        ident = {"id": uuid.UUID(herstel)}
        rij = "SELECT status, attempts FROM commands WHERE id=:id"
        assert await sql(url, rij, ident) == [("queued", 0)]
        monkeypatch.setattr(connector_changes, "LOCAL_PROPOSAL", volgende)
        monkeypatch.setattr(connector_transition, "LOCAL_PROPOSAL", volgende)

        def beschadig(dump):
            dump.with_name(dump.name + ".sha256").write_text("ongeldige controlesom")
            verify(dump)

        with monkeypatch.context() as m:
            m.setattr(cli, "verify_migration_dump", beschadig)
            with pytest.raises(cli.MigrationError, match="SHA-256"):
                await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
        assert await sql(url, rij, ident) == [("queued", 0)]
        archief = await asyncio.to_thread(cli.migrate, url, backup_dir=tmp_path)
        await asyncio.to_thread(verify, archief)
        aantal = len(proef.unit.commands)
        await proef.work()
        assert await sql(url, rij, ident) == [("cancelled", 0)]
        assert (await proef.evidence(herstel))["rollback_reason_code"] == "versiewissel"
        assert len(proef.unit.commands) == aantal
        # De beheerder controleert en herstelt de proefunit vóór het volgende zelfstandige geval.
        proef.unit.state["SetTem"] = 20


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
        opnieuw = await andere_versie({"actie": "migreer", "map": str(mapnaam)}, versie=versie)
        assert opnieuw["geweigerd"] == (versie is not None)
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
