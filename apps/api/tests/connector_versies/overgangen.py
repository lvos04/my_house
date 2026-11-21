"""Regressies met de lokale PostgreSQL-fixtures en de oorspronkelijke applicatieversies."""

import asyncio
import gzip
import ipaddress
import json
import os
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from versie import EINDSCHEMA, andere_versie

from shc_api import backup as bk
from shc_api import cli
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import CommandRecord, ConnectorChange, ConnectorHubPin, Device, Integration, utcnow
from shc_api.worker import Worker
from tests.test_inbedrijfstelling_migratie import sql
from tests.test_ws_backup_format import PW, temp_db

if __name__ != "__main__":
    from tests.test_connector_routes import proef as proef


def eigen_database(url):
    delen = urlsplit(url)
    assert delen.hostname == "localhost" or ipaddress.ip_address(delen.hostname).is_loopback
    assert delen.path.startswith(("/shc_t_", "/shc_tmp_"))
    return cli._libpq(url)[0]


async def eindversie(functie, url, *args):
    eigen_database(url)
    repo = Path(os.environ["PROEF_EINDVERSIE"])
    env = os.environ.copy()
    env.update(SHC_DATABASE_URL=url, PYTHONPATH=os.pathsep.join(str(p) for p in (
        repo / "apps/api/src", repo / "integrations/src", repo / "apps/api",
    )))
    proc = await asyncio.create_subprocess_exec(
        sys.executable, __file__, functie, *map(str, args), env=env, cwd=repo / "apps/api",
        stdout=asyncio.subprocess.PIPE,
    )
    uitvoer, _ = await proc.communicate()
    assert proc.returncode == 0, "De meting met de eindversie kon niet worden uitgevoerd."
    return json.loads(uitvoer)


async def terugval_geblokkeerd(url):
    eigen_database(url)
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            transactie = await conn.begin()
            try:
                await conn.run_sync(lambda sync: command.downgrade(bk.alembic_config(connection=sync), "0006"))
                return False
            except (DBAPIError, cli.MigrationError) as fout:
                if "open of onzeker" not in str(fout):
                    raise
                return True
            finally:
                await transactie.rollback()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("neveneffect", [False, True])
async def test_teruglezing_na_geweigerde_downgrade(proef, monkeypatch, neveneffect):
    voorstel = await proef.propose()
    apply = await proef.post("apply", proef.request(preview=voorstel))
    assert apply.status_code == 202
    await proef.work()
    bewijs = await proef.evidence(voorstel["proposal_id"])
    assert bewijs["status"] == "confirmed"
    assert await terugval_geblokkeerd(get_settings().database_url)
    terug = await proef.post("rollback", proef.request(preview=voorstel, rollback=bewijs["rollback_preview"]))
    assert terug.status_code == 202
    ident = terug.json()["command_id"]
    assert (await proef.evidence(ident))["rollback_status"] == "queued" and proef.unit.state["SetTem"] == 22
    antwoord = proef.unit._reply

    def verander_ventilator(bericht, *args, **kwargs):
        if bericht["t"] == "res":
            proef.unit.state["WdSpd"] = 3
        return antwoord(bericht, *args, **kwargs)

    if neveneffect:
        monkeypatch.setattr(proef.unit, "_reply", verander_ventilator)
    await proef.work()
    na = await proef.evidence(ident)
    print(f"ROLLBACK temperatuur={proef.unit.state['SetTem']} ventilator={na['rollback_after']['controls']['WdSpd']} "
          f"status={na['rollback_status']} reden={na['rollback_reason_code']} herstel_nodig={na['recovery_required']}")
    assert len(proef.unit.commands) == 2 and proef.unit.state["SetTem"] == voorstel["before"]["target_c"]
    if neveneffect:
        assert na["rollback_status"] == "failed" and na["rollback_reason_code"] == "readback_side_effect"
        assert na["rollback_after"]["controls"]["WdSpd"] == 3 and na["recovery_required"]
    else:
        assert na["rollback_after"] == na["before"] and na["rollback_status"] == "confirmed"
        assert not na["recovery_required"]


async def test_bekende_ventilator_ontbreekt(proef, monkeypatch):
    adapter = next(iter(proef.worker.adapters.values()))
    update, *_ = await adapter.poll()
    assert update.state["caps"]["fan"] and update.state["fan"] == "auto"
    async with sessionmaker()() as db:
        apparaat = await db.get(Device, proef.device_id)
        apparaat.state = {**apparaat.state, **update.state}
        await db.commit()

    antwoord = proef.unit._reply
    ventilator_voor = proef.unit.state["WdSpd"]

    def zonder_ventilator(bericht, *args, **kwargs):
        if bericht["t"] == "dat" and "WdSpd" in bericht["cols"]:
            index = bericht["cols"].index("WdSpd")
            bericht = {**bericht, "cols": list(bericht["cols"]), "dat": list(bericht["dat"])}
            bericht["cols"].pop(index)
            bericht["dat"].pop(index)
        elif bericht["t"] == "res":
            proef.unit.state["WdSpd"] = 3
        return antwoord(bericht, *args, **kwargs)

    monkeypatch.setattr(proef.unit, "_reply", zonder_ventilator)
    voorstel = await proef.propose()
    apply = await proef.post("apply", proef.request(preview=voorstel))
    if apply.status_code >= 400:
        assert voorstel["rollback_recipe"] is None and not proef.unit.commands
        return
    assert apply.status_code == 202
    await proef.work()
    bewijs = await proef.evidence(voorstel["proposal_id"])
    if bewijs["rollback_preview"]:
        terug = await proef.post("rollback", proef.request(preview=voorstel, rollback=bewijs["rollback_preview"]))
        if terug.status_code == 202:
            await proef.work()
    na = await proef.evidence(voorstel["proposal_id"])
    print(f"VENTILATOR voorstel={voorstel['status']} apply={apply.status_code} "
          f"WdSpd_in_bewijs={'WdSpd' in voorstel['before']['controls']} "
          f"voor={ventilator_voor} na={proef.unit.state['WdSpd']} "
          f"rollback={na['rollback_status']} herstel_nodig={na['recovery_required']}")
    assert na["rollback_status"] != "confirmed" or proef.unit.state["WdSpd"] == ventilator_voor


async def test_onderbreking_voor_verzending(proef):
    voorstel = await proef.propose()
    gepland = await proef.post("apply", proef.request(preview=voorstel))
    assert gepland.status_code == 202
    ident = uuid.UUID(gepland.json()["command_id"])
    claim = await proef.worker._claim_next({proef.home_id})
    assert claim[0] == ident and not proef.unit.commands
    async with sessionmaker()() as db:
        cmd = await db.get(CommandRecord, ident)
        assert cmd.status == "sending"
        cmd.updated_at = utcnow() - timedelta(minutes=3)
        await db.commit()
    herstart = Worker()
    herstart.adapters.update(proef.worker.adapters)
    await herstart.process_commands({proef.home_id})
    bewijs = await proef.evidence(voorstel["proposal_id"])
    assert bewijs["reason_code"] == "readback_unknown_after_restart" and not proef.unit.commands
    opties = {"rollback": bewijs["rollback_preview"]} if "rollback_preview" in bewijs else {}
    terug = await proef.post("rollback", proef.request(preview=voorstel, **opties))
    if terug.status_code == 202:
        await herstart.process_commands({proef.home_id})
    na = await proef.evidence(voorstel["proposal_id"])
    blok = await terugval_geblokkeerd(get_settings().database_url)
    print(f"ONDERBREKING stand={proef.unit.state['SetTem']} rollback_http={terug.status_code} "
          f"herstel_nodig={na['recovery_required']} schematerugval_geblokkeerd={blok}")
    assert not na["recovery_required"] and not blok


async def moment(url):
    namen = await sql(url, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
    inhoud = {}
    for (naam,) in namen:
        if naam == "alembic_version":
            continue
        projectie = "to_jsonb(t)"
        if naam in {"devices", "climate_zones"}:
            projectie += " - 'connector_version'"
        rijen = await sql(url, f'SELECT ({projectie})::text FROM "{naam}" t ORDER BY 1')  # noqa: S608 - eigen catalogus
        inhoud[naam] = [rij[0] for rij in rijen]
    seq = await sql(url, "SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY 1")
    return inhoud, seq


async def test_gevulde_upgrade_en_sha_terugweg(tmp_path):
    from tests.test_inbedrijfstelling_behoud import seed_and_bytes

    async with temp_db("0006") as bron, temp_db(None) as doel:
        await seed_and_bytes(bron, seed=True)
        voor, seq = await moment(bron)
        assert voor["users"] and voor["sessions"] and voor["device_bindings"] and voor["schedule_blocks"]
        archief = await asyncio.to_thread(cli.migrate, bron, backup_dir=tmp_path)
        await asyncio.to_thread(cli.verify_migration_dump, archief)
        na, seq_na = await moment(bron)
        assert na.pop("connector_changes") == []
        assert voor == na and seq == seq_na
        await asyncio.to_thread(subprocess.run, ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", eigen_database(doel)],
                       input=gzip.decompress(archief.read_bytes()), capture_output=True, check=True)
        assert await moment(doel) == (voor, seq)
        assert await sql(doel, "SELECT version_num FROM alembic_version") == [("0006",)]
        koppelingen = await sql(doel, """
            SELECT r.key, d.stable_identity FROM device_bindings b JOIN devices d ON d.id=b.device_id
            JOIN logical_roles r ON r.id=b.role_id
        """)
        assert koppelingen == [("proef", "proeflamp")]
        await sql(doel, "UPDATE homes SET name='Negatieve vergelijking'")
        assert (await moment(doel))[0] != voor
        print(f"ARCHIEF tabellen={len(voor)} gevuld={sum(bool(r) for r in voor.values())} "
              f"rijen={sum(map(len, voor.values()))} sequenties={len(seq)} "
              f"SHA256=juist terug=0006 koppelingen={len(koppelingen)} wijziging_gedetecteerd=True")


async def test_herhaalde_schematerugval_na_bevestigd_herstel(proef, tmp_path):
    voorstel = await proef.propose()
    apply = await proef.post("apply", proef.request(preview=voorstel))
    assert apply.status_code == 202
    await proef.work()
    bewijs = await proef.evidence(voorstel["proposal_id"])
    opties = {"rollback": bewijs["rollback_preview"]} if "rollback_preview" in bewijs else {}
    terug = await proef.post("rollback", proef.request(preview=voorstel, **opties))
    assert terug.status_code == 202
    await proef.work()
    bewijs = await proef.evidence(voorstel["proposal_id"])
    assert bewijs["status"] == bewijs["rollback_status"] == "confirmed"
    assert bewijs["before"] == bewijs["rollback_after"] and not bewijs["recovery_required"]
    assert proef.unit.state["SetTem"] == 20 and len(proef.unit.commands) == 2
    bron = get_settings().database_url
    archief = await asyncio.to_thread(cli.pre_migration_dump, bron, "0007", tmp_path)
    await asyncio.to_thread(cli.verify_migration_dump, archief)
    async with temp_db(None) as kopie:
        await asyncio.to_thread(subprocess.run, ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", eigen_database(kopie)],
                       input=gzip.decompress(archief.read_bytes()), capture_output=True, check=True)
        cfg = bk.alembic_config(kopie)
        assert await sql(kopie, "SELECT count(*) FROM connector_changes") == [(1,)]
        await asyncio.to_thread(command.downgrade, cfg, "0006")
        await asyncio.to_thread(command.upgrade, cfg, "0007")
        rijen = await sql(kopie, "SELECT count(*) FROM connector_changes")
        opdrachten = await sql(kopie, "SELECT count(*) FROM commands WHERE source='connector' AND status='confirmed'")
        geblokkeerd = await terugval_geblokkeerd(kopie)
        print(f"TWEEDE_TERUGVAL apparaat={proef.unit.state['SetTem']} eerste_terugval=0006 "
              f"na_upgrade_bewijsrijen={rijen[0][0]} bevestigde_opdrachten={opdrachten[0][0]} "
              f"tweede_terugval_geblokkeerd={geblokkeerd}")
        assert not geblokkeerd


async def test_voorstel_en_wachtende_apply_na_twee_schemawissels(proef, tmp_path):
    eerste = await proef.propose()
    sleutel = proef.key_id
    proef.key_id = str(uuid.uuid4())
    tweede = await proef.propose()
    ontvangst = await proef.post("apply", proef.request(preview=tweede))
    assert ontvangst.status_code == 202 and not proef.unit.commands
    cmd = {"id": uuid.UUID(ontvangst.json()["command_id"])}
    url = get_settings().database_url
    cfg = bk.alembic_config(url)
    ids = {"een": uuid.UUID(eerste["proposal_id"]), "twee": uuid.UUID(tweede["proposal_id"])}
    selecteer = "SELECT to_jsonb(c)::text FROM connector_changes c WHERE id IN (:een,:twee) ORDER BY id"
    voor = await sql(url, selecteer, ids)
    assert len(voor) == 2
    open_database = {"actie": "migreer", "map": str(tmp_path)}
    for _ in range(2):
        await asyncio.to_thread(command.downgrade, cfg, "0006")
        assert await sql(url, "SELECT status FROM commands WHERE id=:id", cmd) == [("cancelled",)]
        for software, schema in (("schema0006", "0006"), ("schema0007b", "0007")):
            gelezen = await andere_versie(open_database, versie=software)
            assert gelezen["schema"] == schema and not gelezen["geweigerd"]
            await andere_versie({"actie": "werk", "woning": str(proef.home_id)}, versie=software)
            assert not proef.unit.commands
        opnieuw = await andere_versie(open_database)
        assert opnieuw["schema"] == EINDSCHEMA and not opnieuw["geweigerd"]
        assert await sql(url, selecteer, ids) == voor
    proef.key_id = sleutel
    antwoord = await proef.post("apply", proef.request(preview=eerste))
    assert antwoord.status_code == 409 and antwoord.json()["code"] == "proposal_unavailable"
    await proef.work()
    assert not proef.unit.commands and proef.unit.state["SetTem"] == eerste["before"]["target_c"]
    print("VOORSTELLEN twee_wissels=True bewijsrijen=2 wachtende_apply=cancelled oud_voorstel=409 apparaatopdrachten=0")


async def test_logische_export_op_0006_neemt_hersteld_bewijs_mee(proef, tmp_path):
    voorstel = await proef.propose()
    assert (await proef.post("apply", proef.request(preview=voorstel))).status_code == 202
    await proef.work()
    bewijs = await proef.evidence(voorstel["proposal_id"])
    assert (await proef.post("rollback", proef.request(preview=voorstel, rollback=bewijs["rollback_preview"]))).status_code == 202
    await proef.work()
    bron = get_settings().database_url
    rij = "SELECT (to_jsonb(c) - 'execution_expires_at' - 'rollback_expires_at')::text FROM connector_changes c WHERE id=:id"
    sleutel = {"id": uuid.UUID(voorstel["proposal_id"])}
    voor = await sql(bron, rij, sleutel)
    assert len(voor) == 1
    async with temp_db(None) as oud, temp_db() as doel:
        archief = await asyncio.to_thread(cli.pre_migration_dump, bron, cli.current_revision(bron), tmp_path)
        await asyncio.to_thread(cli.verify_migration_dump, archief)
        await asyncio.to_thread(subprocess.run, ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", eigen_database(oud)],
                       input=gzip.decompress(archief.read_bytes()), capture_output=True, check=True)
        await asyncio.to_thread(command.downgrade, bk.alembic_config(oud), "0006")
        mapnaam = Path(__file__).parent
        oude_code = Path(os.environ["PROEF_VERSIES"]) / "schema0006"
        omgeving = dict(os.environ, SHC_DATABASE_URL=oud, PYTHONPATH=os.pathsep.join(str(oude_code / pad)
                        for pad in ("apps/api/src", "integrations/src", "apps/api")))
        export = tmp_path / "historie.shcbak"
        proces = await asyncio.create_subprocess_exec(sys.executable, str(mapnaam / "export0006.py"), str(export),
                                                      env=omgeving, cwd=oude_code / "apps/api")
        assert await proces.wait() == 0
        await bk.restore_file(export, PW, url=doel, verified=bk.verify_file(export, PW))
        assert await sql(doel, rij, sleutel) == voor
        assert await sql(doel, "SELECT count(*) FROM connector_hub_pins") == [(0,)]
        assert await sql(doel, "SELECT count(*) FROM commands WHERE source='connector' AND status='confirmed'") == [(2,)]
        assert await sql(doel, "SELECT execution_expires_at, rollback_expires_at FROM connector_changes WHERE id=:id",
                         sleutel) == [(None, None)]
        await asyncio.to_thread(command.downgrade, bk.alembic_config(doel), "0006")
        print("LOGISCHE_EXPORT oude_schrijver=0006 feiten_gelijk=True bevestigde_opdrachten=2 uitvoertermijnen=0 terugval=0006")


async def oud_open_herstel(proef):
    voorstel = await proef.propose()
    for route in ("apply", "rollback"):
        opties = {}
        if route == "rollback":
            bewijs = await proef.evidence(voorstel["proposal_id"])
            if bewijs.get("rollback_preview"):
                opties["rollback"] = bewijs["rollback_preview"]
        verzoek = proef.request(preview=voorstel, **opties)
        verzoek["approval"]["request_id"] = verzoek["proposal"]["idempotency_key"]
        antwoord = await proef.post(route, proef.sign(verzoek))
        assert antwoord.status_code == 202
        if route == "apply":
            await proef.work()
            assert (await proef.evidence(voorstel["proposal_id"]))["status"] == "confirmed"
    assert proef.unit.state["SetTem"] == 22 and len(proef.unit.commands) == 1
    return voorstel, antwoord.json()["command_id"]


async def test_apply_bevestigd_zonder_geplande_rollback(proef, tmp_path):
    vorige_stand = dict(proef.unit.state)
    voorstel = await proef.propose()
    verzoek = proef.request(preview=voorstel)
    verzoek["approval"]["request_id"] = verzoek["proposal"]["idempotency_key"]
    assert (await proef.post("apply", proef.sign(verzoek))).status_code == 202
    await proef.work()
    bewijs = await proef.evidence(voorstel["proposal_id"])
    assert bewijs["status"] == "confirmed"
    assert await sql(get_settings().database_url, "SELECT rollback_command_id FROM connector_changes WHERE id=:id",
                     {"id": uuid.UUID(voorstel["proposal_id"])}) == [(None,)]
    assert proef.unit.state["SetTem"] == 22
    url = get_settings().database_url
    start = await eindversie("upgrade", url, voorstel["proposal_id"], proef.home_id, tmp_path)
    assert start["geweigerd"] and start["schema"] == "0007"
    opties = {"rollback": bewijs["rollback_preview"]} if bewijs.get("rollback_preview") else {}
    herstel = proef.request(preview=voorstel, **opties)
    herstel["approval"]["request_id"] = herstel["proposal"]["idempotency_key"]
    assert (await proef.post("rollback", proef.sign(herstel))).status_code == 202
    await proef.work()
    assert proef.unit.state == vorige_stand and len(proef.unit.commands) == 2
    nieuw = await eindversie("upgrade", url, voorstel["proposal_id"], proef.home_id, tmp_path)
    assert not nieuw["geweigerd"] and nieuw["schema"] == EINDSCHEMA and nieuw["archief"]
    print("APPLY_BEVESTIGD geen_rollback_gepland=True upgrade_geweigerd=True daarna_hersteld=True")


async def test_basis_open_herstel_bij_versiewissel(proef, tmp_path):
    voorstel, _ = await oud_open_herstel(proef)
    url = get_settings().database_url
    resultaat = await eindversie("upgrade", url, voorstel["proposal_id"], proef.home_id, tmp_path)
    print(f"VERSIEWISSEL schema={resultaat['schema']} pin={resultaat['pin']} "
          f"migratie_geweigerd={resultaat['geweigerd']} automatisch_archief={resultaat['archief']} "
          f"rollback={resultaat['rollback']} reden={resultaat['reden']} "
          f"herstelpreview={resultaat['preview']} stand={proef.unit.state['SetTem']}")
    assert not resultaat["geweigerd"] and resultaat["schema"] == EINDSCHEMA
    assert resultaat["reden"] == "versiewissel" and not resultaat["preview"]
    assert proef.unit.state["SetTem"] == 22 and len(proef.unit.commands) == 1


async def test_vorige_installatie_ziet_geannuleerde_rollback(proef, tmp_path):
    voorstel, herstel = await oud_open_herstel(proef)
    url = get_settings().database_url
    gewisseld = await eindversie("upgrade", url, voorstel["proposal_id"], proef.home_id, tmp_path)
    assert not gewisseld["geweigerd"] and gewisseld["archief"] and gewisseld["reden"] == "versiewissel"
    assert (await sql(url, "SELECT status, attempts FROM commands WHERE id=:id", {"id": uuid.UUID(herstel)})) == [
        ("cancelled", 0),
    ]
    await proef.work()
    assert proef.unit.state["SetTem"] == 22 and len(proef.unit.commands) == 1
    archiefproef = await eindversie("schrijf-en-lees", url, tmp_path)
    assert not archiefproef["geweigerd"], "Het afgehandelde oude opdrachtpaar moet als historie teruggezet kunnen worden."
    async with temp_db(None) as kopie:
        await asyncio.to_thread(subprocess.run, ["pg_restore", "--single-transaction", "--dbname", eigen_database(kopie),
                        str(tmp_path / "nieuwe.pgdump")], capture_output=True, check=True)
        key = {"id": uuid.UUID(voorstel["proposal_id"])}
        selecteer = "SELECT to_jsonb(c)::text FROM connector_changes c WHERE id=:id"
        assert await sql(kopie, selecteer, key) == await sql(url, selecteer, key)
        assert await sql(kopie, "SELECT count(*) FROM connector_hub_pins") == [(0,)]


async def test_basis_oud_archief_zonder_connectoropdracht(proef, tmp_path):
    url = get_settings().database_url
    pad, plain = tmp_path / "basis.shcdump", tmp_path / "voor.pgdump"
    assert await sql(url, "SELECT count(*) FROM commands WHERE source='connector'") == [(0,)]
    await asyncio.to_thread(bk._pg_dump_sync, url, pad, PW, utcnow().isoformat())
    await asyncio.to_thread(bk.decrypt_dump, pad, PW, plain)
    async with temp_db(None) as oud:
        await asyncio.to_thread(subprocess.run, ["pg_restore", "--single-transaction", "--dbname", eigen_database(oud), str(plain)],
                       check=True, capture_output=True)
        assert await sql(oud, "SELECT count(*) FROM devices WHERE id=:id", {"id": proef.device_id}) == [(1,)]
        assert await sql(oud, "SELECT count(*) FROM commands WHERE source='connector'") == [(0,)]
    resultaat = await eindversie("lees-archief", url, pad, tmp_path / "na.pgdump")
    print(f"OUD_ARCHIEF apparaat_hersteld_met_basis=1 connectoropdrachten=0 "
          f"eindversie_weigert={resultaat['geweigerd']}")
    assert not resultaat["geweigerd"]


async def test_basis_eerder_teruggezette_opdracht_in_nieuwe_dump(proef, tmp_path):
    voorstel, undo = await oud_open_herstel(proef)
    url = get_settings().database_url
    export = tmp_path / "oude-export.shcbak"
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await bk.write_backup(conn, export, PW, include_secrets=False)
    finally:
        await engine.dispose()
    async with temp_db() as eerder, temp_db(None) as doel:
        await bk.restore_file(export, PW, url=eerder, verified=bk.verify_file(export, PW))
        eis = {"id": uuid.UUID(undo)}
        assert await sql(eerder, "SELECT status FROM commands WHERE id=:id", eis) == [("queued",)]
        assert await sql(eerder, "SELECT count(*) FROM connector_changes") == [(0,)]
        resultaat = await eindversie("schrijf-en-lees", eerder, tmp_path)
        if resultaat["geweigerd"]:
            print("DUMP_ZONDER_BEWIJS eindversie_weigert=True")
            return
        pad = tmp_path / "nieuwe.pgdump"
        await asyncio.to_thread(subprocess.run, ["pg_restore", "--single-transaction", "--dbname", eigen_database(doel), str(pad)],
                       check=True, capture_output=True)
        rijen = await sql(doel, "SELECT count(*) FROM connector_changes WHERE id=:id",
                          {"id": uuid.UUID(voorstel["proposal_id"])})
        opdrachten = await sql(doel, "SELECT status FROM commands WHERE id=:id", eis)
        print(f"DUMP_ZONDER_BEWIJS vrijgegeven=True herstelopdracht={opdrachten[0][0]} "
              f"bewijsrijen={rijen[0][0]} stand={proef.unit.state['SetTem']}")
        assert rijen[0][0] == 1


async def meten_met_eindversie(actie, args):
    """Meet in een nieuw proces met de normale applicatiecode van de eindcommit."""
    url = get_settings().database_url
    eigen_database(url)
    if actie == "upgrade":
        from shc_integrations.gree import GreeAdapter

        from shc_api import connector_changes
        from shc_api.errors import require_secret_box

        ident, woning, mapnaam = args
        geweigerd = False
        archief = None
        try:
            archief = await asyncio.to_thread(cli.migrate, url, backup_dir=Path(mapnaam))
        except cli.MigrationError:
            geweigerd = True
        if not geweigerd:
            worker = Worker()
            async with sessionmaker()() as db:
                koppeling = await db.scalar(select(Integration).where(Integration.home_id == uuid.UUID(woning)))
                assert koppeling.provider == "gree" and ipaddress.ip_address(koppeling.config["host"]).is_loopback
                worker.adapters[koppeling.id] = GreeAdapter(
                    koppeling.config, require_secret_box().decrypt(koppeling.credentials_enc),
                )
            await worker.process_commands({uuid.UUID(woning)})
        async with sessionmaker()() as db:
            wijziging = await db.get(ConnectorChange, uuid.UUID(ident))
            pin = await db.get(ConnectorHubPin, 1)
            bewijs = await connector_changes.evidence(db, wijziging.client, wijziging.id)
        return {"schema": cli.current_revision(url), "pin": pin is not None, "geweigerd": geweigerd,
                "archief": archief is not None, "rollback": bewijs["rollback_status"],
                "reden": bewijs["rollback_reason_code"], "preview": bool(bewijs["rollback_preview"])}
    if actie == "lees-archief":
        pad, plain = map(Path, args)
    elif actie == "schrijf-en-lees":
        mapnaam = Path(args[0])
        pad, plain = mapnaam / "nieuwe.shcdump", mapnaam / "nieuwe.pgdump"
    else:
        raise ValueError("Onbekende lokale meetproef.")
    try:
        if actie == "schrijf-en-lees":
            await asyncio.to_thread(bk._pg_dump_sync, url, pad, PW, utcnow().isoformat())
        await asyncio.to_thread(bk.decrypt_dump, pad, PW, plain)
    except bk.BackupError as fout:
        if "connectorherstelbewijs" not in str(fout):
            raise
        return {"geweigerd": True}
    return {"geweigerd": False}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(meten_met_eindversie(sys.argv[1], sys.argv[2:]))))
