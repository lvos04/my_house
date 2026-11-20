"""Een bronarchief uit de uitgebrachte versie met alleen een momentopname van de Zigbee-map."""
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tarfile
from pathlib import Path

import httpx
import pytest

from shc_api.config import get_settings
from shc_api.db import dispose
from shc_api.main import create_app
from shc_api.routers import system

from .conftest import Session
from .restore_approval import reviewed
from .test_herreview import richt_bron_in, status_taak
from .test_herstelpoorten import proefinstallatie
from .test_ws_backup_api import _upload
from .test_ws_backup_format import PW, temp_db
from .test_ws_backup_restore import _scalar

REPO = Path(__file__).resolve().parents[3]
BRONVERSIE = "e22286cd3620b5e9d31e8a0acf0841892b9ab04b"


@pytest.fixture
def oude_app(tmp_path, request):
    app = tmp_path / "vorige-versie"
    app.mkdir()
    args = ["git", "archive", getattr(request, "param", BRONVERSIE), "VERSION", "apps/api/src", "integrations/src"]
    with subprocess.Popen(args, cwd=REPO, stdout=subprocess.PIPE) as proc:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as files:
            files.extractall(app, filter="data")
        assert proc.wait() == 0
    return app


@pytest.mark.parametrize(("oude_app", "opslagbewijs"), [
    (BRONVERSIE, "verified_empty"),
    ("140d39df39b670b064e663a920a650f03bd9c9d0", "write_fenced_empty"),
], indirect=["oude_app"])
async def test_oude_export_met_zigbee_na_export(tmp_path, monkeypatch, oude_app, opslagbewijs):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "backup_dir", tmp_path / "doel")
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0)
    zigbee = tmp_path / "zigbee2mqtt"
    zigbee.mkdir()
    archive = tmp_path / "oud.shcbak"
    async with proefinstallatie(monkeypatch) as source:
        assert (await source.post("/homes", {"name": "Bron met oude export"})).is_success
        zin = secrets.token_urlsafe(24)
        assert (await source.post("/auth/users", {"username": "lezer", "role": "viewer", "password": zin})).is_success
        bron_url = cfg.database_url
        env = dict(os.environ, SHC_DATABASE_URL=bron_url, SHC_DATA_DIR=str(tmp_path / "app"),
                   SHC_ZIGBEE2MQTT_DIR=str(zigbee),
                   PYTHONPATH=os.pathsep.join(str(oude_app / d) for d in ("apps/api/src", "integrations/src")))
        script = """
import asyncio, sys
from pathlib import Path
from shc_api import backup, migration_source
from shc_api.db import engine, sessionmaker

async def export():
    assert Path(backup.__file__).is_relative_to(Path.cwd())
    async with sessionmaker()() as db:
        await migration_source.pause(db, 'testbeheer')
    async with engine().connect() as conn:
        result = await backup.write_backup(conn, Path(sys.argv[1]), sys.stdin.read(), True, source_proof={})
        assert result['source_pause']['zigbee_store'] == sys.argv[2]
        assert 'stopped_at' not in result['source_pause']
asyncio.run(export())
"""
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", script, str(archive), opslagbewijs, cwd=oude_app,
                                                    env=env, stdin=asyncio.subprocess.PIPE)
        await proc.communicate(PW.encode())
        assert proc.returncode == 0
        if opslagbewijs == "verified_empty":
            (zigbee / "database.db").write_text("synthetische late apparaatstand")
        else:
            login = await source.c.post("/api/v1/auth/login", json={"username": "lezer", "password": zin})
            assert login.is_success
        async with proefinstallatie(monkeypatch) as target:
            verify = await _upload(target, archive.read_bytes(), PW)
            assert verify.is_success
            upload = {"upload_id": verify.json()["upload_id"], "passphrase": PW}
            drill = (await target.post("/system/restore/drill", upload)).json()
            assert drill["data_ok"] and not drill["drill_id"] and "Bronvolledigheid onbekend" in drill["migration_refusal"]
            assert (await target.post("/system/restore/apply", {**upload, "mode": "migration"})).status_code == 409
            assert (await target.get("/system/restore/compare")).json().get("migration") is None
            assert await _scalar(cfg.database_url, "SELECT count(*) FROM homes") == 0
            assert await _scalar(bron_url, "SELECT name FROM homes") == "Bron met oude export"
            # De gewone herstelroute voor oudere archieven houdt de drie poorten.
            proposal = await reviewed(target, {**upload, "mode": "same_home"})
            assert proposal["rollback_id"] and proposal["confirmation"]


BASIS_EXPORT = """
import asyncio, json, sys
from pathlib import Path
from shc_api import backup, cli

opt = json.load(sys.stdin)
assert Path(backup.__file__).is_relative_to(Path(opt['basis']))
if opt['actie'] == 'schema':
    cli.migrate(opt['url'], pre_dump=False)
else:
    result = asyncio.run(backup.run_backup(opt['zin'], url=opt['url'], backup_dir=Path(opt['map']),
                                            z2m_dir=Path(opt['zigbee'])))
    print(result['filename'])
"""


async def vorige_versie(basis, actie, **velden):
    omgeving = dict(os.environ, PYTHONPATH=os.pathsep.join(str(basis / d) for d in ("apps/api/src", "integrations/src")))
    proces = await asyncio.create_subprocess_exec(
        sys.executable, "-c", BASIS_EXPORT, cwd=basis, env=omgeving,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    uit, _ = await proces.communicate(json.dumps(dict(actie=actie, basis=str(basis), **velden)).encode())
    assert proces.returncode == 0, "De basisversie kon de lokale testbewerking niet uitvoeren."
    return uit.decode().strip()


STEEKPROEF = """SELECT jsonb_build_object(
    'gebruikers', (SELECT jsonb_agg(to_jsonb(u) ORDER BY id) FROM users u),
    'woningen', (SELECT jsonb_agg(to_jsonb(h) ORDER BY id) FROM homes h),
    'bindingen', (SELECT jsonb_agg(to_jsonb(b) ORDER BY id) FROM device_bindings b),
    'telefoons', (SELECT jsonb_agg(to_jsonb(c) ORDER BY id) FROM api_clients c),
    'metingen', (SELECT jsonb_agg(to_jsonb(s) ORDER BY id) FROM sensor_readings s))"""


@pytest.mark.parametrize("oude_app", ["41f7dab30d820eb33036566a306c054f06c1a4c1"], indirect=True)
async def test_basisarchief_en_terugval(tmp_path, monkeypatch, oude_app):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "backup_dir", tmp_path / "doel")
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0)
    transport = httpx.ASGITransport(app=create_app())
    async with temp_db(None) as bron, temp_db() as doel:
        try:
            await vorige_versie(oude_app, "schema", url=bron)
            await dispose()
            monkeypatch.setattr(cfg, "database_url", bron)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                _, pw, telefoon, _ = await richt_bron_in(c)
            bronstaat = await _scalar(bron, STEEKPROEF)
            assert [len(bronstaat[k]) for k in ("gebruikers", "woningen", "bindingen", "telefoons", "metingen")] == [2, 1, 1, 1, 2]
            zmap = tmp_path / "oude-zigbee"
            zmap.mkdir()
            naam = await vorige_versie(oude_app, "export", url=bron, map=str(tmp_path), zigbee=str(zmap), zin=PW)
            inhoud = (tmp_path / naam).read_bytes()
            sha = hashlib.sha256(inhoud).digest()
            await dispose()
            monkeypatch.setattr(cfg, "database_url", doel)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                r = await c.post("/api/v1/auth/setup", json={"username": "doel", "password": pw})
                assert r.status_code == 200
                admin = Session(c, r.json()["csrf_token"])
                bestand = (await _upload(admin, inhoud, PW)).json()
                body = {"upload_id": bestand["upload_id"], "passphrase": PW}
                proef = (await admin.post("/system/restore/drill", body)).json()
                assert proef["data_ok"] and not proef["drill_id"] and all(x["ok"] for x in proef["tables"].values())
                print(f"Basisarchief: {len(proef['tables'])} tabellen gecontroleerd; zonder bronpauze geen migratieakkoord.")
                aanvraag = await admin.post("/system/restore/apply", await reviewed(admin, {**body, "mode": "same_home"}))
                assert await status_taak(admin, aanvraag) == "done"
                assert await _scalar(doel, STEEKPROEF) == bronstaat == await _scalar(bron, STEEKPROEF)
                assert hashlib.sha256((tmp_path / naam).read_bytes()).digest() == sha
            for locatie in (doel, bron):
                await dispose()
                monkeypatch.setattr(cfg, "database_url", locatie)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                    assert (await c.get("/api/v1/homes", headers={"Authorization": "Bearer " + telefoon})).status_code == 200
                    assert (await c.post("/api/v1/auth/login", json={"username": "kijker", "password": pw})).status_code == 200
            print("Basis naar eindversie: SHA-256 gelijk; 2 gebruikers, 1 woning, 1 binding, 1 telefoon, 2 metingen inhoudelijk gelijk. Kijker en telefoon lezen op doel en bron.")
        finally:
            await dispose()
