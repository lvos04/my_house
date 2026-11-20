"""Regressies voor versiegebonden bediening, historische opdrachten en volledig herstelbewijs."""

import asyncio
import gzip
import subprocess
import uuid

import pytest
from versie import andere_versie

from shc_api import backup as bk
from shc_api import cli
from shc_api.config import get_settings
from tests.test_connector_routes import proef as proef
from tests.test_inbedrijfstelling_migratie import sql
from tests.test_ws_backup_format import PW, temp_db


async def route(proef, pad, verzoek=None):
    return await andere_versie({
        "actie": "route", "methode": "GET" if verzoek is None else "POST", "route": pad,
        "koppen": {**proef.admin.h(), "Idempotency-Key": str(uuid.uuid4())},
        "sessie": dict(proef.admin.c.cookies), "verzoek": verzoek,
    })


@pytest.mark.parametrize("alleen_voorstel", [False, True], ids=["met_opdracht", "enkel_voorstel"])
async def test_oude_stille_stand_blijft_bekend_bij_versiewissel(proef, monkeypatch, tmp_path, alleen_voorstel):
    from tests.test_connector_herstel import _apply, _queue_rollback

    if alleen_voorstel:
        eerste = await proef.propose()
    else:
        eerste, _ = await _apply(proef)
        await _queue_rollback(proef, eerste)
        await proef.work()
        oud = await proef.evidence(eerste["proposal_id"])
        assert oud["rollback_after"] == oud["before"] and oud["rollback_status"] == "confirmed"
    assert eerste["before"]["controls"]["Quiet"] == 0

    veldquery = "SELECT state->'gree_control_fields' FROM devices WHERE id=:id"
    apparaat = {"id": proef.device_id}
    if alleen_voorstel:
        # De oorspronkelijke applicatie schreef de meting alleen bij het voorstel in de logische export.
        archief = await bk.run_backup(PW, backup_dir=tmp_path, z2m_dir=tmp_path / "lege-opslag")
        async with temp_db(None) as doel:
            await andere_versie({"actie": "herstel_export", "pad": str(tmp_path / archief["filename"])}, url=doel)
            hersteld = await sql(doel, veldquery, apparaat)
            assert eerste["before"]["controls"].keys() <= set(hersteld[0][0] or [])

    migratie = await andere_versie({"actie": "migreer", "map": str(tmp_path)})
    assert not migratie["geweigerd"]
    oud_veld = await sql(get_settings().database_url, """
        SELECT before->'controls'->'Quiet' FROM connector_changes WHERE id=:id
    """, {"id": uuid.UUID(eerste["proposal_id"])})
    assert oud_veld == [(0,)]
    bewaarde_velden = await sql(get_settings().database_url, veldquery, apparaat)
    assert eerste["before"]["controls"].keys() <= set(bewaarde_velden[0][0] or [])
    assert await sql(get_settings().database_url, "SELECT count(*) FROM idempotency_keys WHERE key=:id",
                     {"id": uuid.UUID(eerste["proposal_id"])}) == [(0,)]
    proef.key_id = str(uuid.uuid4())
    volledig = await route(proef, "propose", proef.request())
    assert volledig["http"] == 200 and volledig["inhoud"]["status"] == "proposed"
    assert volledig["inhoud"]["before"] == eerste["before"]
    antwoord = proef.unit._reply

    def zonder_stille_stand(bericht, *args, **kwargs):
        if bericht["t"] == "dat" and "Quiet" in bericht["cols"]:
            index = bericht["cols"].index("Quiet")
            bericht = {**bericht, "cols": list(bericht["cols"]), "dat": list(bericht["dat"])}
            bericht["cols"].pop(index)
            bericht["dat"].pop(index)
        elif bericht["t"] == "res":
            proef.unit.state["Quiet"] = 1
        return antwoord(bericht, *args, **kwargs)

    monkeypatch.setattr(proef.unit, "_reply", zonder_stille_stand)
    aantal = len(proef.unit.commands)
    proef.key_id = str(uuid.uuid4())
    voorstel = await route(proef, "propose", proef.request())
    assert voorstel["http"] == 200
    inhoud = voorstel["inhoud"]
    apply = await route(proef, "apply", proef.request(preview=inhoud))
    assert inhoud["status"] == "failed" and inhoud["reason_code"] == "readback_unavailable"
    assert inhoud["rollback_recipe"] is None and inhoud["before"] is None
    assert apply["http"] == 409 and apply["inhoud"]["code"] == "readback_unavailable"
    assert len(proef.unit.commands) == aantal and proef.unit.state["Quiet"] == 0
    assert (await sql(get_settings().database_url, "SELECT command_id FROM connector_changes WHERE id=:id",
                      {"id": uuid.UUID(inhoud["proposal_id"])})) == [(None,)]
    print("VELD_NA_WISSEL oud_Quiet=0 leesfout=True apply=409 nieuwe_opdrachten=0 Quiet_na=0")


async def test_oude_afgehandelde_opdracht_houdt_upgrade_vrij(proef, tmp_path):
    from tests.test_connector_herstel import _apply, _queue_rollback

    # Dit eerdere goedgekeurde verzoek handhaafde de actuele, volledig teruggelezen 20 graden.
    sleutel = proef.key_id
    eerste = await proef.propose(params={"target_c": 20})
    ontvangen = await proef.post("apply", proef.request(params={"target_c": 20}, preview=eerste))
    assert ontvangen.status_code == 202
    await proef.work()
    bewijs = await proef.evidence(eerste["proposal_id"])
    assert bewijs["status"] == "confirmed" and bewijs["before"] == bewijs["after"]
    assert not bewijs["recovery_required"]
    proef.key_id = str(uuid.uuid4())
    tweede, _ = await _apply(proef)
    await _queue_rollback(proef, tweede)
    await proef.work()
    na = await proef.evidence(tweede["proposal_id"])
    assert na["rollback_status"] == "confirmed" and na["rollback_after"] == bewijs["before"]
    assert not na["recovery_required"] and proef.unit.state["SetTem"] == 20

    # Ook de oude installatie krijgt de kans om de gevraagde afhandeling alsnog uit te voeren.
    proef.key_id = sleutel
    opnieuw = await proef.evidence(eerste["proposal_id"])
    antwoord = await proef.post("rollback", proef.request(
        params={"target_c": 20}, preview=eerste, rollback=opnieuw["rollback_preview"],
    ))
    assert antwoord.status_code == 409 and antwoord.json()["code"] == "precondition_failed"
    migratie = await andere_versie({"actie": "migreer", "map": str(tmp_path)})
    await andere_versie({"actie": "terugval", "schema": "0008"})
    voorloper = await andere_versie({"actie": "migreer", "map": str(tmp_path)}, versie="schema0008")
    assert not voorloper["geweigerd"] and voorloper["schema"] == "0008"
    zelfde_schema = await andere_versie({"actie": "migreer", "map": str(tmp_path)})
    print(f"HISTORIE stand=20 eerste_voor_gelijk_na=True laatste_rollback=confirmed "
          f"oud_herstel={antwoord.status_code} upgrade_geweigerd={migratie['geweigerd']} "
          f"voorganger={voorloper['schema']} eind_op_0008_geweigerd={zelfde_schema['geweigerd']}")
    assert not migratie["geweigerd"] and not zelfde_schema["geweigerd"]
    bekend = await route(proef, "evidence/" + eerste["proposal_id"])
    assert bekend["inhoud"]["before"] == bekend["inhoud"]["after"] == bewijs["before"]
    assert bekend["inhoud"]["rollback_preview"] is None
    assert proef.unit.state["SetTem"] == 20 and len(proef.unit.commands) == 3


async def test_dump_weigert_verlies_van_een_vastgesteld_veld(proef, tmp_path):
    voorstel = await proef.propose()
    opdracht = await proef.post("apply", proef.request(preview=voorstel))
    assert opdracht.status_code == 202
    assert voorstel["before"]["controls"]["WdSpd"] == 0 and not proef.unit.commands
    url = get_settings().database_url
    ident = {"id": uuid.UUID(voorstel["proposal_id"])}
    bekend = await sql(url, "SELECT state->'gree_control_fields' FROM devices WHERE id=:id",
                       {"id": proef.device_id})
    assert "WdSpd" in bekend[0][0]
    # Negatieve integriteitsproef: één veld uit de vorige stand gaat verloren in de eigen fixture.
    await sql(url, """UPDATE connector_changes SET before=before #- '{controls,WdSpd}' WHERE id=:id""", ident)
    with pytest.raises(bk.BackupError, match="connectorherstelbewijs"):
        await bk.run_db_dump(PW, auto=False, url=url, backup_dir=tmp_path)
    assert not list(tmp_path.glob("*.shcdump"))
    # Ook een reeds weggeschreven archief moet bij ontsleuteling worden geweigerd.
    encrypted = tmp_path / "onvolledig.shcdump"
    await asyncio.to_thread(bk._pg_dump_sync, url, encrypted, PW, voorstel["observed_at"])
    out = tmp_path / "bestaand.pgdump"
    out.write_bytes(b"eerder gecontroleerde uitvoer")
    with pytest.raises(bk.BackupError, match="connectorherstelbewijs"):
        await asyncio.to_thread(bk.decrypt_dump, encrypted, PW, out, url=url)
    assert out.read_bytes() == b"eerder gecontroleerde uitvoer"
    print("DUMP_VELD aanmaken_geweigerd=True ontsleutelen_geweigerd=True uitvoer_behouden=True")


async def test_terug_naar_vorige_worker_met_open_rollback(proef, tmp_path):
    from tests.test_connector_herstel import _apply, _queue_rollback

    voorstel, _ = await _apply(proef)
    herstel_id = await _queue_rollback(proef, voorstel)
    voor = await proef.evidence(herstel_id)
    assert voor["rollback_status"] == "queued" and proef.unit.state["SetTem"] == 22
    url = get_settings().database_url
    ident = {"id": uuid.UUID(herstel_id)}
    claim = "SELECT status, attempts, updated_at FROM commands WHERE id=:id"
    oorspronkelijk = await sql(url, claim, ident)
    assert oorspronkelijk[0][:2] == ("queued", 0)
    for _ in range(2):
        await andere_versie({"actie": "terugval", "schema": "0008"})
        oud = await andere_versie({"actie": "migreer", "map": str(tmp_path)}, versie="schema0008")
        assert not oud["geweigerd"] and oud["schema"] == "0008"
        await andere_versie({"actie": "werk", "woning": str(proef.home_id), "controleer_start": True},
                            versie="schema0008")
        assert (await sql(url, claim, ident))[0][:2] == ("cancelled", 0)
        assert proef.unit.state["SetTem"] == 22 and len(proef.unit.commands) == 1
        nieuw = await andere_versie({"actie": "migreer", "map": str(tmp_path)})
        assert not nieuw["geweigerd"] and nieuw["schema"] == "0009"
    await proef.work()
    na = await proef.evidence(herstel_id)
    assert na["rollback_command_id"] == herstel_id and na["rollback_reason_code"] == "versiewissel"
    assert na["before"] == voor["before"] and na["rollback_after"] is None and na["recovery_required"]
    assert proef.unit.state["SetTem"] == 22 and len(proef.unit.commands) == 1


async def test_oude_vervallen_opdracht_laat_nieuwe_backups_beschikbaar(proef, tmp_path):
    voorstel = await proef.propose()
    assert "controls" not in voorstel["before"]
    bericht = proef.request(preview=voorstel)
    bericht["approval"]["request_id"] = bericht["proposal"]["idempotency_key"]
    gepland = await proef.post("apply", proef.sign(bericht))
    assert gepland.status_code == 202
    await sql(get_settings().database_url, """
        UPDATE commands SET created_at=created_at - interval '4 minutes' WHERE id=:id
    """, {"id": uuid.UUID(gepland.json()["command_id"])})
    await proef.work()
    bewijs = await proef.evidence(voorstel["proposal_id"])
    assert bewijs["reason_code"] == "refused_before_send" and not proef.unit.commands
    assert proef.unit.state["SetTem"] == 20
    migratie = await andere_versie({"actie": "migreer", "map": str(tmp_path)})
    assert not migratie["geweigerd"] and migratie["archief"]

    def keuze(mapnaam):
        return {"actie": "dump", "map": str(tmp_path / mapnaam), "voorstel": voorstel["proposal_id"]}

    ouders = await andere_versie(keuze("vorig"), versie="schema0008")
    assert not ouders["geweigerd"]
    status, reden, doel = ouders["rijen"][0][:3]
    assert status == "failed" and reden == "refused_before_send" and float(doel) == 20
    nieuw = await andere_versie(keuze("nieuw"))
    print(f"OUDE_WEIGERING apparaatopdrachten=0 stand=20 reden={reden} "
          f"migratie={migratie['schema']} vorige_dump_teruggezet=True nieuwe_dump_geweigerd={nieuw['geweigerd']}")
    assert not nieuw["geweigerd"] and nieuw["rijen"] == ouders["rijen"]
    assert nieuw["rijen"][0][3] == gepland.json()["command_id"]
    assert nieuw["rijen"][0][4] == voorstel["before"] and nieuw["rijen"][0][5] == 0
    assert not proef.unit.commands and proef.unit.state["SetTem"] == 20


async def test_oude_0007_leest_bewaard_bewijs_na_schemawissel(proef, tmp_path):
    from alembic import command

    from tests.test_connector_herstel import _apply, _queue_rollback

    voorstel, _ = await _apply(proef)
    await _queue_rollback(proef, voorstel)
    await proef.work()
    bewijs = await proef.evidence(voorstel["proposal_id"])
    assert bewijs["rollback_status"] == "confirmed" and bewijs["rollback_after"] == bewijs["before"]
    url = get_settings().database_url
    archief = await asyncio.to_thread(cli.pre_migration_dump, url, cli.current_revision(url), tmp_path)
    await asyncio.to_thread(cli.verify_migration_dump, archief)
    async with temp_db(None) as kopie:
        await asyncio.to_thread(subprocess.run, ["psql", "-Xq", "-v", "ON_ERROR_STOP=1", "-d",
                                cli._libpq(kopie)[0]], input=gzip.decompress(archief.read_bytes()),
                                check=True, capture_output=True)
        ident = {"id": uuid.UUID(voorstel["proposal_id"])}
        rij = "SELECT to_jsonb(c)::text, command_id::text, rollback_command_id::text FROM connector_changes c WHERE id=:id"
        voor = await sql(kopie, rij, ident)
        assert voor[0][1:] == (bewijs["command_id"], bewijs["rollback_command_id"])
        opdracht = {"actie": "migreer", "map": str(tmp_path)}
        for ronde in range(2):
            await asyncio.to_thread(command.downgrade, bk.alembic_config(kopie), "0006")
            oud6 = await andere_versie(opdracht, versie="schema0006", url=kopie)
            assert oud6["schema"] == "0006" and not oud6["geweigerd"]
            bewaard = await sql(kopie, "SELECT (value->'v')::text FROM settings WHERE key=:key",
                                {"key": "connector_history:" + voorstel["proposal_id"]})
            assert bewaard == [(voor[0][0],)]
            oud7 = await andere_versie(opdracht, versie="schema0007b", url=kopie)
            assert oud7["schema"] == "0007" and not oud7["geweigerd"]
            assert await sql(kopie, "SELECT count(*) FROM settings WHERE key=:key",
                             {"key": "connector_history:" + voorstel["proposal_id"]}) == [(1,)]
            if ronde == 0:
                # Een afgebroken start mag de enige bewaarde bewijsrij niet uit settings verwijderen.
                herstel_id = {"id": uuid.UUID(bewijs["rollback_command_id"])}
                await sql(kopie, "UPDATE commands SET status='sending' WHERE id=:id", herstel_id)
                geweigerd = await andere_versie(opdracht, url=kopie)
                assert geweigerd["geweigerd"] and geweigerd["schema"] == "0007"
                assert await sql(kopie, rij, ident) == []
                assert await sql(kopie, "SELECT (value->'v')::text FROM settings WHERE key=:key",
                                 {"key": "connector_history:" + voorstel["proposal_id"]}) == bewaard
                await sql(kopie, "UPDATE commands SET status='confirmed' WHERE id=:id", herstel_id)
            nieuw = await andere_versie(opdracht, url=kopie)
            assert not nieuw["geweigerd"] and nieuw["schema"] == "0009"
            assert await sql(kopie, rij, ident) == voor
            assert await sql(kopie, "SELECT count(*) FROM commands WHERE source='connector' AND status='confirmed'") == [(2,)]
            assert await sql(kopie, "SELECT count(*) FROM idempotency_keys WHERE key=:id", ident) == [(0,)]
        assert proef.unit.state["SetTem"] == 20 and len(proef.unit.commands) == 2
        print("OUDE_0007 twee_wissels=True bewijsrij_gelijk=True beide_opdracht_ids_gelijk=True uitvoerrechten=0")
