"""Lokale herstelpoorten op een gevuld doel en het taakjournaal bij verlies van het API-proces."""
import asyncio
import json
import os
import secrets
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from shc_api import backup as bk
from shc_api.config import get_settings
from shc_api.db import dispose, sessionmaker
from shc_api.main import create_app
from shc_api.models import Home
from shc_api.routers import system

from .conftest import Session
from .restore_approval import reviewed, verify_three_gates
from .test_ws_backup_api import _upload
from .test_ws_backup_format import PW, temp_db


@asynccontextmanager
async def proefinstallatie(monkeypatch):
    async with temp_db() as url:
        await dispose()
        with monkeypatch.context() as local:
            local.setattr(get_settings(), "database_url", url)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as c:
                    r = await c.post("/api/v1/auth/setup", json={"username": "beheer", "password": secrets.token_urlsafe(24)})
                    assert r.status_code == 200
                    yield Session(c, r.json()["csrf_token"])
            finally:
                await dispose()


@pytest.fixture
async def doelbeheerder(monkeypatch):
    async with proefinstallatie(monkeypatch) as beheerder:
        yield beheerder


@pytest.mark.parametrize("mode", ["same_home", "new_home"])
async def test_gevuld_doel_vergt_drie_poorten(doelbeheerder, mode, tmp_path, monkeypatch):
    admin = doelbeheerder
    home = (await admin.post("/homes", {"name": "Oorspronkelijke woning"})).json()
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0)
    cfg = get_settings()
    monkeypatch.setattr(cfg, "backup_dir", tmp_path)
    if mode == "new_home":
        async with proefinstallatie(monkeypatch) as bron:
            foreign = (await bron.post("/homes", {"name": "Afzonderlijke bronwoning"})).json()
            assert foreign["id"] != home["id"]
            saved = await bk.run_backup(PW)
    else:
        saved = await bk.run_backup(PW)
    path = tmp_path / saved["filename"]
    upload = (await _upload(admin, path.read_bytes(), PW)).json()
    body = {"upload_id": upload["upload_id"], "passphrase": PW, "mode": mode}
    # Beide routes vervangen een doel met evenveel woningen, maar met andere inhoud.
    async with sessionmaker()() as db:
        existing = await db.get(Home, uuid.UUID(home["id"]))
        existing.name = "Doel na export"
        await db.commit()
    proof = await verify_three_gates(admin, body)
    other = await reviewed(admin, body)
    assert (await admin.post("/system/restore/apply", {**proof, "confirmation": other["confirmation"]})).status_code == 409
    async with sessionmaker()() as db:
        existing = await db.get(Home, uuid.UUID(home["id"]))
        existing.name = "Nieuwere doelwaarde"
        await db.commit()
    assert (await admin.post("/system/restore/apply", proof)).status_code == 409
    response = await admin.post("/system/restore/preview", body)
    preview = response.json()
    counts = preview["comparison"]["tables"]["homes"]
    assert counts["before"] == counts["after"] and not counts["same"]
    rollback = tmp_path / "terugval" / (preview["comparison"]["rollback_id"] + ".shcbak")
    target = bk.verify_file(rollback, PW)
    assert "Nieuwere doelwaarde" in {h["name"] for h in target.summary["homes"]}
    result = await admin.post("/system/restore/apply", {**body, "rollback_id": preview["comparison"]["rollback_id"],
                                                        "confirmation": preview["confirmation"]})
    assert result.status_code == 202, result.text
    async with asyncio.timeout(30):
        while (job := (await admin.get("/system/restore/jobs/" + result.json()["job_id"])).json())["state"] == "running":
            await asyncio.sleep(0.05)
    assert job["state"] == "done", job
    # De terugvalkopie is ook na het terugzetten leesbaar en herstelbaar in een afzonderlijke database.
    async with temp_db() as url:
        await bk.restore_file(rollback, PW, url=url)
        from .test_ws_backup_restore import _scalar
        assert await _scalar(url, "SELECT count(*) FROM homes WHERE name='Nieuwere doelwaarde'") == 1
    async with sessionmaker()() as db:
        assert "Nieuwere doelwaarde" not in await db.scalars(select(Home.name))


async def _process():
    # Een afzonderlijk proces bootst verlies van alleen onze test-API na. De ouder bezit de proefdatabase.
    opts = json.load(sys.stdin)
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            if opts["phase"] == "restart":
                job = (await c.get("/api/v1/system/restore/jobs/" + opts["job_id"])).json()
                async with sessionmaker()() as db:
                    from shc_api.store import get_setting
                    values = [await get_setting(db, k) for k in ("maintenance", "safe_mode", "restored_from")]
                    homes = list(await db.scalars(select(Home.name).order_by(Home.name)))
                print(json.dumps([job["state"], values, job["error"], homes]), flush=True)
                return
            r = await c.post("/api/v1/auth/setup", json={"username": "proef", "password": opts["password"]})
            admin = Session(c, r.json()["csrf_token"])
            home = await admin.post("/homes", {"name": "Herstartproef doelwoning"})
            assert home.status_code == 200
            upload = (await _upload(admin, await asyncio.to_thread(Path(opts["archive"]).read_bytes), PW)).json()
            body = await reviewed(admin, {"upload_id": upload["upload_id"], "passphrase": PW, "mode": "same_home"})
            if opts["phase"] == "after":
                system.MAINTENANCE_SETTLE_S = 0
                system._remove_upload = lambda _: os._exit(0)  # na de COMMIT, vóór opruimen
            result = await admin.post("/system/restore/apply", body)
            assert result.status_code == 202, result.text
            print(json.dumps(result.json()["job_id"]), flush=True)
            if opts["phase"] == "before":
                os._exit(0)  # na de 202, vóór de vervanging
            await asyncio.sleep(30)
            raise AssertionError("De databasevervanging bereikte het herstartpunt niet.")


@pytest.mark.parametrize("phase", ["before", "after"])
async def test_herstartpunt(doelbeheerder, phase, tmp_path):
    assert (await doelbeheerder.post("/homes", {"name": "Herstartproef bronwoning"})).status_code == 200
    saved = await bk.run_backup(PW, backup_dir=tmp_path)
    async with temp_db() as url:
        env = dict(os.environ, SHC_DATABASE_URL=url, SHC_BACKUP_DIR=str(tmp_path / "doel"),
                   SHC_DATA_DIR=str(tmp_path / "app"), SHC_TEST_CHILD_DATABASE=url)
        # conftest stelt bij import de pytest-URL in. Herstel daarna uitsluitend de eigen tijdelijke database.
        code = ("from tests.test_herstelpoorten import _process; import asyncio, os; "
                "from shc_api.config import get_settings; "
                "os.environ['SHC_DATABASE_URL'] = os.environ['SHC_TEST_CHILD_DATABASE']; "
                "get_settings.cache_clear(); asyncio.run(_process())")

        async def run(action, **fields):
            proc = await asyncio.create_subprocess_exec(sys.executable, "-c", code, env=env,
                                                       stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE)
            info = dict(phase=action, archive=str(tmp_path / saved["filename"]), password=secrets.token_urlsafe(24))
            stdout, stderr = await proc.communicate(json.dumps({**info, **fields}).encode())
            assert proc.returncode == 0, stderr.decode().splitlines()[-1:]
            return json.loads(stdout.decode().splitlines()[-1])

        job_id = await run(phase)
        state, (maintenance, safe, restored), error, homes = await run("restart", job_id=job_id)
        assert not maintenance
        if phase == "before":
            assert state == "failed" and "vóór de databasevervanging" in error
            assert not safe and not restored
            assert homes == ["Herstartproef doelwoning"]
        else:
            assert state == "done" and safe and restored["mode"] == "same_home"
            assert homes == ["Herstartproef bronwoning"]


def test_compose_zigbee_koppelt_de_verwachte_bron():
    from shc_api.migration_source import compose_zigbee_mount
    app = "1 0 8:1 /proef/app /data rw - ext4 schijf rw"
    zigbee = "2 1 8:1 /proef/zigbee2mqtt /data/zigbee2mqtt ro - ext4 schijf rw"
    assert compose_zigbee_mount(app + "\n" + zigbee)
    assert not compose_zigbee_mount(app)
    assert not compose_zigbee_mount(app + "\n" + zigbee.replace("/proef/", "/andere/"))


async def test_cli_vereist_de_getoonde_vergelijking(doelbeheerder, tmp_path, monkeypatch, capsys):
    import re

    from shc_api import cli

    cfg = get_settings()
    monkeypatch.setattr(cfg, "backup_dir", tmp_path)
    monkeypatch.setenv("SHC_BACKUP_PASSPHRASE", PW)
    home = (await doelbeheerder.post("/homes", {"name": "Woning in archief"})).json()
    record = await bk.run_backup(PW)
    path = tmp_path / record["filename"]
    async with sessionmaker()() as db:
        h = await db.get(Home, uuid.UUID(home["id"]))
        h.name = "Woning op doel"
        await db.commit()
    args = cli.build_parser().parse_args(["restore", str(path), "--yes"])
    assert await cli._restore(args, dry_run=False) == 3
    shown = capsys.readouterr().out
    assert "Woning op doel" in shown and "Woning in archief" in shown
    match = re.search(r"--rollback-id ([\w-]+) --confirmation ([a-f0-9]{64})", shown)
    assert match
    args.rollback_id = uuid.UUID(match[1])
    assert await cli._restore(args, dry_run=False) == 2
    args.confirmation = match[2]
    assert await cli._restore(args, dry_run=False) == 0
    async with sessionmaker()() as db:
        assert (await db.get(Home, uuid.UUID(home["id"]))).name == "Woning in archief"
