"""Behoud van getypeerde waarden en toegang tot de bron bij een publicatiefout."""
import errno
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text

from shc_api import backup, migration_inventory, migration_source
from shc_api.config import get_settings
from shc_api.db import dispose, sessionmaker
from shc_api.main import create_app
from shc_api.models import Device
from shc_api.routers import system

from .conftest import Session
from .restore_approval import reviewed
from .test_behoud_r3 import stel_opslag_in
from .test_getypeerde_archiefrijen import RANDWAARDEN
from .test_herreview import richt_bron_in, status_taak
from .test_herstelpoorten import proefinstallatie
from .test_ws_backup_api import _upload
from .test_ws_backup_format import PW, temp_db
from .test_ws_backup_restore import _scalar


async def test_catalogus_per_tabel(monkeypatch):
    reden = {
        "users": {"failed_logins", "locked_until"},
        "api_clients": {"last_used_at"},
        "devices": {"online", "last_seen", "connector_version"},
        "integrations": {"state", "detail", "last_ok_at"},
        "climate_zones": {"connector_version"},
    }
    lokaal = {"sessions", "pairing_codes", "processed_events", "connector_hub_pins",
              "connector_nonces", "idempotency_keys", "alembic_version"}
    async with proefinstallatie(monkeypatch), sessionmaker()() as db:
        catalogus = (await db.execute(text("""
            SELECT table_name, column_name, data_type
              FROM information_schema.columns WHERE table_schema='public'
             ORDER BY table_name, ordinal_position
        """))).all()
        conn = await db.connection()
        opname = await conn.run_sync(migration_inventory.capture)
        namen = {naam for naam, _, _ in catalogus}
        assert set(opname) == namen - lokaal
        assert {soort for naam, _, soort in catalogus if naam in opname} <= RANDWAARDEN.keys()
        for naam in sorted(opname):
            alle = {veld for tabel, veld, _ in catalogus if tabel == naam}
            jsonvelden = {veld for tabel, veld, soort in catalogus if tabel == naam and soort in {"json", "jsonb"}}
            gemeten = set(opname[naam]["columns"])
            assert gemeten == alle - reden.get(naam, set())
            assert jsonvelden <= gemeten



STAND = """SELECT array_agg(CASE WHEN raw IS NULL THEN 'SQL-NULL'
                         WHEN jsonb_typeof(raw)='null' THEN 'JSON-null' ELSE 'ingevuld' END
                         ORDER BY id) FROM sensor_readings"""


async def test_ontbrekende_en_expliciet_lege_meetinhoud(monkeypatch, tmp_path):
    cfg = get_settings()
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0)
    transport = httpx.ASGITransport(app=create_app())
    async with temp_db() as bron, temp_db() as doel:
        try:
            await dispose()
            monkeypatch.setattr(cfg, "database_url", bron)
            stel_opslag_in(monkeypatch, tmp_path / "bron")
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                beheer, zin, _, apparaat = await richt_bron_in(client)
                async with sessionmaker()() as db:
                    await db.execute(text("""
                        UPDATE sensor_readings
                           SET raw = CASE WHEN id=(SELECT min(id) FROM sensor_readings)
                                          THEN NULL::jsonb ELSE 'null'::jsonb END
                    """))
                    await db.commit()
                voor = await _scalar(bron, STAND)
                assert voor == ["SQL-NULL", "JSON-null"]
                assert (await beheer.post("/system/restore/source/pause")).status_code == 200
                export = await beheer.post("/system/restore/source/export", {"passphrase": PW, "stop_source": True})
                assert export.status_code == 200
                pad = cfg.backup_dir / "terugval" / (export.json()["file_id"] + ".shcbak")
                staten = [json.loads(regel) for naam, deel in backup.iter_members(pad, PW)
                          if naam.startswith("tables/sensor_readings/") for regel in deel.read().splitlines()]
                assert [r["raw"] for r in staten] == [None, "null"]
                download = await beheer.get("/system/restore/files/" + export.json()["file_id"])
                assert download.status_code == 200

            await dispose()
            monkeypatch.setattr(cfg, "database_url", doel)
            stel_opslag_in(monkeypatch, tmp_path / "doel")
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                tijdelijk = await client.post("/api/v1/auth/setup", json={"username": "tijdelijk", "password": zin})
                assert tijdelijk.status_code == 200
                beheer = Session(client, tijdelijk.json()["csrf_token"])
                upload = await _upload(beheer, download.content, PW)
                assert upload.status_code == 200
                body = {"upload_id": upload.json()["upload_id"], "passphrase": PW}
                proef = await beheer.post("/system/restore/drill", body)
                assert proef.status_code == 200
                gegevens = proef.json()
                assert gegevens["drill_id"] and gegevens["data_ok"]
                akkoord = await reviewed(beheer, {**body, "mode": "migration", "drill_id": gegevens["drill_id"]})
                opdracht = await beheer.post("/system/restore/apply", akkoord)
                assert await status_taak(beheer, opdracht) == "done"
                aanmelding = await client.post("/api/v1/auth/login", json={"username": "beheer", "password": zin})
                assert aanmelding.status_code == 200
                beheer = Session(client, aanmelding.json()["csrf_token"])
                async with sessionmaker()() as db:
                    lamp = await db.get(Device, apparaat)
                    lamp.online, lamp.last_seen = True, datetime.now(UTC)
                    await db.commit()
                assert await _scalar(doel, STAND) == await _scalar(bron, STAND) == voor
                vergelijking = (await beheer.get("/system/restore/compare")).json()
                assert vergelijking["inventory"]["sensor_readings"] == {"source": 2, "target": 2, "same": True}
                eerder = vergelijking["migration"]["confirmation"]
                assert eerder
                async with sessionmaker()() as db:
                    await db.execute(text("UPDATE sensor_readings SET raw='null'::jsonb WHERE raw IS NULL"))
                    await db.commit()
                assert await _scalar(doel, STAND) == ["JSON-null", "JSON-null"]
                antwoord = await beheer.post("/system/restore/confirm", {"confirmation": eerder, "source_stopped": True})
                assert antwoord.status_code == 409
                verschil = (await beheer.get("/system/restore/compare")).json()
                assert verschil["inventory"]["sensor_readings"] == {"source": 2, "target": 2, "same": False}
                assert verschil["migration"]["confirmation"] is None
                # Ook een open overstap van vóór de update moet opnieuw op de bron beginnen.
                async with sessionmaker()() as db:
                    await db.execute(text("UPDATE sensor_readings SET raw=NULL WHERE id=(SELECT min(id) FROM sensor_readings)"))
                    await db.execute(text("""UPDATE settings SET value=jsonb_set(value,
                        '{v,source_pause,inventory_version}', '2') WHERE key='migration_pending'"""))
                    await db.commit()
                oud = (await beheer.get("/system/restore/compare")).json()
                assert not any(r["same"] for r in oud["inventory"].values())
                assert oud["migration"]["confirmation"] is None and "SQL NULL" in oud["migration"]["refusal"]
        finally:
            await dispose()


async def test_eerdere_download_bij_fout_in_mapbevestiging(monkeypatch, tmp_path):
    stel_opslag_in(monkeypatch, tmp_path)
    async with proefinstallatie(monkeypatch) as beheer:
        assert (await beheer.post("/homes", {"name": "Proefwoning"})).status_code == 200
        assert (await beheer.post("/system/restore/source/pause")).status_code == 200
        eerder = await beheer.post("/system/restore/source/export", {"passphrase": PW})
        assert eerder.status_code == 200
        code = eerder.json()["file_id"]
        route = "/system/restore/files/" + code
        voor = await beheer.get(route)
        assert voor.status_code == 200
        controle = hashlib.sha256(voor.content).digest()
        pad = get_settings().backup_dir / "terugval" / (code + ".shcbak")
        assert backup.verify_file(pad, PW).manifest["tables"]["homes"]["rows"] == 1
        mapinfo = pad.parent.stat()
        fsync = os.fsync
        storingen = 0

        def weiger_alleen_mapbevestiging(fd):
            nonlocal storingen
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (mapinfo.st_dev, mapinfo.st_ino):
                storingen += 1
                raise OSError(errno.EIO, "Gesimuleerde fout bij het bevestigen van de archiefmap")
            return fsync(fd)

        transport = httpx.ASGITransport(app=create_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=beheer.c.cookies) as client:
            sessie = Session(client, beheer.csrf)
            with monkeypatch.context() as fout:
                fout.setattr(os, "fsync", weiger_alleen_mapbevestiging)
                tussen = await sessie.post("/system/restore/source/export", {"passphrase": PW})
                assert tussen.status_code == 409 and list(pad.parent.glob("*.shcbak")) == [pad]
                vorige = await sessie.get(route)
                assert vorige.status_code == 200 and hashlib.sha256(vorige.content).digest() == controle
                nieuw = await sessie.post("/system/restore/source/export", {"passphrase": PW, "stop_source": True})
                assert nieuw.status_code == 409 and storingen > 0
                tijdens = await sessie.get(route)
                assert tijdens.status_code == 200 and hashlib.sha256(tijdens.content).digest() == controle
                onbevestigd, = (p for p in pad.parent.glob("*.shcbak") if p != pad)
                assert (await sessie.get("/system/restore/files/" + onbevestigd.stem)).status_code == 409
                stand = await sessie.get("/system/settings")
                assert stand.status_code == 200 and not stand.json()["source_pause"].get("file_id")
                assert stand.json()["source_pause"]["interim_file_id"] == code
                assert (await sessie.post("/system/restore/source/resume")).status_code == 409
                assert (await sessie.get("/homes")).status_code == 200
                assert hashlib.sha256(pad.read_bytes()).digest() == controle

                class NaDeVervaltijd(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return super().now(tz) + timedelta(hours=2)

                fout.setattr(migration_source, "datetime", NaDeVervaltijd)
                hervatten = await sessie.post("/system/restore/source/resume")
                assert hervatten.status_code == 200 and hervatten.json()["paused"] is False
                assert (await sessie.post("/homes", {"name": "Bron hervat"})).status_code == 200
                onzekere_route = "/system/restore/files/" + onbevestigd.stem
                assert (await sessie.get(onzekere_route)).status_code == 409
                vorige = await sessie.get(route)
                assert vorige.status_code == 200 and hashlib.sha256(vorige.content).digest() == controle
            bevestigd = await sessie.get(onzekere_route)
            assert bevestigd.status_code == 200 and bevestigd.content == onbevestigd.read_bytes()
            na = await sessie.get(route)
        assert na.status_code == 200 and hashlib.sha256(na.content).digest() == controle
