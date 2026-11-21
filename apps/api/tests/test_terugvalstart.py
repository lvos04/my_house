"""Vrijgave van dezelfde database en terugval met een afgesloten wizardbron."""

import asyncio
import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError

from shc_api import backup, cli, migration_source
from shc_api.config import get_settings
from shc_api.db import dispose

from .test_behoud_r3 import stel_opslag_in
from .test_herstelpoorten import proefinstallatie
from .test_inbedrijfstelling_migratie import sql
from .test_ws_backup_format import PW, temp_db

START = Path(__file__).resolve().parents[3] / "apps/api/src/shc_api/terugval_start.py"
BEHOUD = """SELECT (SELECT array_agg(id ORDER BY id) FROM users), (SELECT array_agg(id ORDER BY id) FROM homes),
    (SELECT array_agg(id ORDER BY id) FROM sessions), (SELECT count(*) FROM commands WHERE source='connector')"""
PAUZE = "SELECT value FROM settings WHERE key='migration_source_pause'"


async def test_terugval_id_vereist_dezelfde_kopie_en_laatste_geslaagde_voorbereiding():
    async def start(url, receipt):
        env = dict(os.environ, SHC_DATABASE_URL=url, SHC_TERUGVAL_ID=str(receipt))
        proc = await asyncio.to_thread(subprocess.run, [sys.executable, str(START), "terugval", "migrate"],
                                       env=env, capture_output=True, text=True, timeout=45)
        if proc.returncode:
            assert "Terugval geweigerd" in proc.stderr
        return proc.returncode

    async with temp_db() as kopie, temp_db() as andere:
        eerste = await cli.prepare_connector_fallback(kopie, issue_receipt=True)
        tweede = await cli.prepare_connector_fallback(andere, issue_receipt=True)
        assert eerste != tweede
        assert await start(kopie, "") == await start(kopie, tweede) == 2
        assert await start(kopie, eerste) == 0
        await sql(kopie, "UPDATE alembic_version SET version_num='proef_onbekend'")
        with pytest.raises(cli.MigrationError, match="onbekend"):
            await cli.prepare_connector_fallback(kopie, issue_receipt=True)
        await sql(kopie, "UPDATE alembic_version SET version_num=:rev", {"rev": backup.head_revision()})
        assert await start(kopie, eerste) == 2
        opnieuw = await cli.prepare_connector_fallback(kopie, issue_receipt=True)
        assert opnieuw != eerste and await start(kopie, opnieuw) == 0


async def kopieer(bron, kopie, archief):
    await asyncio.to_thread(subprocess.run, ["pg_dump", "-Fc", "-f", str(archief), cli._libpq(bron)[0]],
                            check=True, capture_output=True)
    await asyncio.to_thread(subprocess.run, ["pg_restore", "--single-transaction", "-d", cli._libpq(kopie)[0],
                                             str(archief)], check=True, capture_output=True)


@pytest.mark.parametrize("met_voorstelrecht", [False, True])
async def test_gepauzeerde_terugvalkopie_na_eindexport(monkeypatch, tmp_path, met_voorstelrecht):
    class Eerder(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) - timedelta(hours=2)

    cfg = get_settings()
    zigbee = stel_opslag_in(monkeypatch, tmp_path / "bron")
    try:
        async with proefinstallatie(monkeypatch) as beheer:
            assert (await beheer.post("/homes", {"name": "Proefwoning"})).is_success
            bron = cfg.database_url
            if met_voorstelrecht:
                await sql(bron, """INSERT INTO idempotency_keys(scope,key,request_hash)
                          VALUES ('connector:voorstel',gen_random_uuid(),sha256('voorstel'::bytea))""")
            with monkeypatch.context() as tijd:
                tijd.setattr(migration_source, "datetime", Eerder)
                pauze = await beheer.post("/system/restore/source/pause")
                assert pauze.status_code == 200
                eind = await beheer.post("/system/restore/source/export", {"stop_source": True, "passphrase": PW})
                assert eind.status_code == 200
            assert datetime.fromisoformat(pauze.json()["expires"]) < datetime.now(UTC)
            bestand = cfg.backup_dir / "terugval" / (eind.json()["file_id"] + ".shcbak")
            await asyncio.to_thread(backup.verify_file, bestand, PW)
            sha = hashlib.sha256(bestand.read_bytes()).digest()
            voor, = await sql(bron, BEHOUD)
            assert all(len(ids) == 1 for ids in voor[:3]) and voor[3] == 0
            grens = await sql(bron, PAUZE)
            async with temp_db(None) as kopie:
                await kopieer(bron, kopie, tmp_path / "gepauzeerd.dump")
                if met_voorstelrecht:
                    with pytest.raises(cli.MigrationError, match="Hervat de oorspronkelijke bron"):
                        await cli.prepare_connector_fallback(kopie, issue_receipt=True)
                    assert await sql(kopie, "SELECT count(*) FROM idempotency_keys WHERE scope='connector:voorstel'") == [(1,)]
                else:
                    assert await cli.prepare_connector_fallback(kopie, issue_receipt=True)
                for url in (bron, kopie):
                    with pytest.raises(DBAPIError) as fout:
                        await sql(url, "UPDATE homes SET name=name")
                    assert fout.value.orig.sqlstate == "25006"
                    assert await sql(url, PAUZE) == grens and await sql(url, BEHOUD) == [voor]
                if not met_voorstelrecht:
                    await dispose()
                    with monkeypatch.context() as apart:
                        apart.setattr(cfg, "database_url", kopie)
                        stel_opslag_in(apart, tmp_path / "kopie")
                        hervat = await beheer.post("/system/restore/source/resume")
                        assert hervat.status_code == 409
                        assert "nieuwe offline terugvalkopie" in hervat.json()["detail"]["message"]
                        await dispose()
                assert await sql(kopie, PAUZE) == grens and await sql(kopie, BEHOUD) == [voor]
            # De oude worker blijft uit terwijl de beheerder op de oorspronkelijke opslag hervat.
            assert (await beheer.post("/system/restore/source/resume")).status_code == 200
            async with temp_db(None) as nieuw:
                await kopieer(bron, nieuw, tmp_path / "hervat.dump")
                assert await cli.prepare_connector_fallback(nieuw, issue_receipt=True)
                assert await sql(nieuw, PAUZE) == [] and await sql(nieuw, BEHOUD) == [voor]
                assert await sql(nieuw, "SELECT count(*) FROM idempotency_keys WHERE scope='connector:voorstel'") == [(0,)]
            assert await sql(bron, BEHOUD) == [voor] and hashlib.sha256(bestand.read_bytes()).digest() == sha
    finally:
        zigbee.chmod(0o700)
