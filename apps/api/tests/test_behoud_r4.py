"""Vaste gegevensbehoudproeven voor de bronexport en de bevestiging op het doel."""
import asyncio
import errno
import json
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from shc_api import backup, cli, migration_source, restore_control
from shc_api.config import get_settings
from shc_api.db import dispose, sessionmaker
from shc_api.main import create_app
from shc_api.models import Device
from shc_api.routers import system

from .conftest import Session
from .restore_approval import reviewed
from .test_behoud_r3 import stel_opslag_in
from .test_herreview import richt_bron_in, status_taak
from .test_herstelpoorten import proefinstallatie
from .test_inbedrijfstelling_migratie import sql
from .test_ws_backup_api import _upload
from .test_ws_backup_format import PW, temp_db
from .test_ws_backup_restore import _scalar

KALIBRATIE = "19.1234567890123456789"
LEES_GETAL = "SELECT diagnostics #>> '{proef,kalibratie}' FROM devices WHERE id=:id"
SCHRIJF_GETAL = text("""UPDATE devices SET diagnostics = jsonb_set(diagnostics, '{proef}',
    jsonb_build_object('kalibratie', CAST(:getal AS numeric))) WHERE id=:id""")
EINDEXPORT = {"passphrase": PW, "stop_source": True}


async def breid_proefdatabases_uit(bron, doel, monkeypatch):
    # Alleen deze twee wegwerpdatabases en de proefdatabase van dit verzoek krijgen de extra tabel.
    ddl = "CREATE TABLE commissioning_gate (id integer PRIMARY KEY, notitie jsonb NOT NULL)"
    for url in (bron, doel):
        await sql(url, ddl)
    migreer = cli.migrate

    def met_extra_tabel(url, *, pre_dump):
        migreer(url, pre_dump=pre_dump)
        asyncio.run(sql(url, ddl))

    monkeypatch.setattr(cli, "migrate", met_extra_tabel)


@pytest.mark.parametrize("geval", ["jsongetal", "nieuwe_tabel"])
async def test_eindvergelijking_bewaart_alle_inhoud(geval, tmp_path, monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0)
    transport = httpx.ASGITransport(app=create_app())
    async with temp_db() as bron, temp_db() as doel:
        try:
            if geval == "nieuwe_tabel":
                await breid_proefdatabases_uit(bron, doel, monkeypatch)
            await dispose()
            monkeypatch.setattr(cfg, "database_url", bron)
            stel_opslag_in(monkeypatch, tmp_path / "bron")
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                beheer, zin, _, apparaat = await richt_bron_in(client)
                async with sessionmaker()() as db:
                    if geval == "jsongetal":
                        await db.execute(SCHRIJF_GETAL, {"getal": KALIBRATIE, "id": apparaat})
                    else:
                        await db.execute(text("""INSERT INTO commissioning_gate
                            VALUES (1, '{"keuring":{"opmerking":"Proefwoning gecontroleerd"}}')"""))
                    await db.commit()
                assert (await beheer.post("/system/restore/source/pause")).is_success
                export = await beheer.post("/system/restore/source/export", EINDEXPORT)
                assert export.status_code == 200
                bestand = export.json()["file_id"]
                download = await beheer.get("/system/restore/files/" + bestand)
                assert download.status_code == 200
                if geval == "jsongetal":
                    archief = cfg.backup_dir / "terugval" / (bestand + ".shcbak")
                    rijen = [json.loads(regel, parse_float=Decimal) for naam, deel in backup.iter_members(archief, PW)
                             if naam.startswith("tables/devices/") for regel in deel.read().splitlines()]
                    rij, = (r for r in rijen if r["id"] == str(apparaat))
                    assert rij["diagnostics"]["proef"]["kalibratie"] == Decimal(KALIBRATIE)
                    assert await _scalar(bron, LEES_GETAL, id=apparaat) == KALIBRATIE
                    bewijs = backup.verify_file(archief, PW).manifest["source_pause"]
                    ouder = {k: v for k, v in bewijs.items() if k != "inventory_version"}
                    assert migration_source.complete(bewijs, bewijs["source_id"])
                    assert not migration_source.complete(ouder, bewijs["source_id"])

            await dispose()
            monkeypatch.setattr(cfg, "database_url", doel)
            stel_opslag_in(monkeypatch, tmp_path / "doel")
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                setup = await client.post("/api/v1/auth/setup", json={"username": "tijdelijk", "password": zin})
                beheer = Session(client, setup.json()["csrf_token"])
                upload = await _upload(beheer, download.content, PW)
                body = {"upload_id": upload.json()["upload_id"], "passphrase": PW}
                proef = (await beheer.post("/system/restore/drill", body)).json()
                assert proef["data_ok"] and proef["drill_id"]
                akkoord = await reviewed(beheer, {**body, "mode": "migration", "drill_id": proef["drill_id"]})
                assert await status_taak(beheer, await beheer.post("/system/restore/apply", akkoord)) == "done"
                login = await client.post("/api/v1/auth/login", json={"username": "beheer", "password": zin})
                beheer = Session(client, login.json()["csrf_token"])
                async with sessionmaker()() as db:
                    lamp = await db.get(Device, apparaat)
                    lamp.online, lamp.last_seen = True, datetime.now(UTC)
                    await db.commit()
                vooraf = (await beheer.get("/system/restore/compare")).json()
                eerder = vooraf["migration"]["confirmation"]
                assert eerder
                async with sessionmaker()() as db:
                    if geval == "jsongetal":
                        assert await _scalar(doel, LEES_GETAL, id=apparaat) == KALIBRATIE
                        await db.execute(SCHRIJF_GETAL, {"getal": KALIBRATIE[:-1] + "8", "id": apparaat})
                        tabel, aantal_doel = "devices", 1
                    else:
                        lees_notitie = "SELECT notitie #>> '{keuring,opmerking}' FROM commissioning_gate"
                        assert await _scalar(doel, lees_notitie) == await _scalar(bron, lees_notitie) == "Proefwoning gecontroleerd"
                        await db.execute(text("DELETE FROM commissioning_gate WHERE id=1"))
                        tabel, aantal_doel = "commissioning_gate", 0
                    await db.commit()
                # Eerst het bestaande akkoord indienen. Een nieuwe vergelijking zou dat akkoord zelf al vervangen.
                geweigerd = await beheer.post("/system/restore/confirm", {"source_stopped": True, "confirmation": eerder})
                assert geweigerd.status_code == 409
                na = (await beheer.get("/system/restore/compare")).json()
                assert na["inventory"][tabel] == {"source": 1, "target": aantal_doel, "same": False}
                assert na["commissioning_gate"]["same"] and na["migration"]["confirmation"] is None
        finally:
            await dispose()


async def test_gewone_export_bewaart_json_en_jsonreeks(tmp_path):
    async with temp_db() as bron, temp_db() as doel:
        for url in (bron, doel):
            await sql(url, "CREATE TABLE proefmeting (id int PRIMARY KEY, meting json, reeks jsonb[])")
        meting = '{"getal":' + KALIBRATIE + ',"getal":2}'
        groot = "9" * 4600
        await sql(bron, "INSERT INTO proefmeting VALUES (1, CAST(:meting AS json), ARRAY[CAST(:groot AS jsonb)])",
                  {"meting": meting, "groot": groot})
        bestand = await backup.run_backup(PW, url=bron, backup_dir=tmp_path)
        await backup.restore_file(tmp_path / bestand["filename"], PW, url=doel)
        terug = await _scalar(doel, "SELECT meting::text FROM proefmeting")
        assert json.loads(terug, parse_float=Decimal, object_pairs_hook=list) == [
            ("getal", Decimal(KALIBRATIE)), ("getal", 2)]
        assert await _scalar(doel, "SELECT reeks[1]::text FROM proefmeting") == groot


async def test_geweigerde_publicatie_houdt_bron_bereikbaar(monkeypatch, tmp_path):
    stel_opslag_in(monkeypatch, tmp_path)
    async with proefinstallatie(monkeypatch) as beheer:
        zin = secrets.token_urlsafe(24)
        assert (await beheer.post("/auth/users", {"username": "lezer", "role": "viewer", "password": zin})).is_success
        assert (await beheer.post("/system/restore/source/pause")).is_success
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as lezer:
            aanmelden = {"username": "lezer", "password": zin}
            assert (await lezer.post("/api/v1/auth/login", json=aanmelden)).is_success
            vervang = Path.replace

            def publicatie_faalt(pad, doel):
                if pad.suffix == ".partial" and Path(doel).parent == tmp_path / "backups" / "terugval":
                    raise OSError(errno.ENOSPC, "Synthetische schrijffout bij het publiceren")
                return vervang(pad, doel)

            with monkeypatch.context() as moment:
                moment.setattr(Path, "replace", publicatie_faalt)
                antwoord = await beheer.post("/system/restore/source/export", EINDEXPORT)
            assert antwoord.status_code == 409
            assert not list((tmp_path / "backups" / "terugval").iterdir())
            assert (await lezer.get("/api/v1/homes")).status_code == 200
            lezer.cookies.clear()
            assert (await lezer.post("/api/v1/auth/login", json=aanmelden)).status_code == 200
            assert (await beheer.post("/system/restore/source/resume")).status_code == 200
            assert (await beheer.post("/homes", {"name": "Bron hervat"})).is_success
            assert await _scalar(get_settings().database_url,
                                 "SELECT count(*) FROM audit_log WHERE actor='lezer' AND action='login'") == 2


@pytest.mark.parametrize("archief_aanwezig", [False, True])
async def test_publicatie_onderbroken_rond_hernoemen(monkeypatch, tmp_path, archief_aanwezig):
    stel_opslag_in(monkeypatch, tmp_path)
    async with proefinstallatie(monkeypatch) as beheer:
        zin = secrets.token_urlsafe(24)
        assert (await beheer.post("/auth/users", {"username": "lezer", "role": "viewer", "password": zin})).is_success
        assert (await beheer.post("/system/restore/source/pause")).is_success
        vervang = Path.replace

        def onderbreek(pad, doel):
            if archief_aanwezig:
                vervang(pad, doel)
            # De webaanvraag valt weg zonder de uitkomst in PostgreSQL vast te leggen.
            raise RuntimeError("Publicatie onderbroken")

        with monkeypatch.context() as moment:
            moment.setattr(Path, "replace", onderbreek)
            with pytest.raises(RuntimeError, match="Publicatie onderbroken"):
                await beheer.post("/system/restore/source/export", EINDEXPORT)
        await dispose()
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            reactie = await client.post("/api/v1/auth/login", json={"username": "lezer", "password": zin})
        hervatten = await beheer.post("/system/restore/source/resume")
        assert reactie.status_code == hervatten.status_code == (409 if archief_aanwezig else 200)
        bestanden = list(restore_control.rollback_dir().glob("*.shcbak"))
        assert len(bestanden) == int(archief_aanwezig)
        if archief_aanwezig:
            stand = (await beheer.get("/system/settings")).json()["source_pause"]
            assert stand["file_id"] == bestanden[0].stem
            assert (await beheer.get("/system/restore/files/" + stand["file_id"])).is_success
