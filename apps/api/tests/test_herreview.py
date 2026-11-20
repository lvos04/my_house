"""Behoudproeven voor bronpauze, dumpimport, vergelijking en onderbroken herstel."""
import asyncio
import contextlib
import io
import json
import os
import re
import secrets
import shlex
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import httpx
import pytest
from sqlalchemy import select, text

from shc_api import backup, cli
from shc_api.config import get_settings
from shc_api.db import dispose, sessionmaker
from shc_api.main import create_app
from shc_api.models import ApiClient, ButtonMapping, Device, DeviceBinding, Home, LogicalRole, SensorReading, User
from shc_api.routers import system
from shc_api.security import new_token, token_hash
from shc_api.store import ensure_server_identity, set_setting

from .conftest import Session
from .restore_approval import reviewed, verify_three_gates
from .test_herstelpoorten import proefinstallatie
from .test_ws_backup_api import _upload
from .test_ws_backup_format import PW, _admin_dsn, temp_db
from .test_ws_backup_restore import _scalar

REPO = Path(__file__).resolve().parents[3]


def meting(apparaat, waarde):
    return SensorReading(device_id=apparaat, metric="power", value=waarde, unit="W",
                         event_id=uuid.uuid4().hex, observed_at=datetime.now(UTC))


async def status_taak(admin, aanvraag):
    assert aanvraag.status_code == 202
    pad = "/system/restore/jobs/" + aanvraag.json()["job_id"]
    async with asyncio.timeout(30):
        while True:
            taak = (await admin.get(pad)).json()
            if taak["state"] != "running":
                return taak["state"]
            await asyncio.sleep(0.05)


async def richt_bron_in(client):
    pw = secrets.token_urlsafe(32)
    setup = await client.post("/api/v1/auth/setup", json={"username": "beheer", "password": pw})
    assert setup.status_code == 200
    admin = Session(client, setup.json()["csrf_token"])
    hid = uuid.UUID((await admin.post("/homes", {"name": "Bronwoning"})).json()["id"])
    assert (await admin.post("/auth/users", {"username": "kijker", "role": "viewer", "password": pw})).is_success
    telefoon = new_token()
    async with sessionmaker()() as db:
        await db.run_sync(ensure_server_identity)
        lamp = Device(id=uuid.uuid4(), home_id=hid, provider="hue", kind="light_group", name="Proeflamp",
                      stable_identity=uuid.uuid4().hex, capabilities=["power"])
        db.add(lamp)
        await db.flush()
        rol = (await db.scalars(select(LogicalRole).where(LogicalRole.home_id == hid))).first()
        db.add(DeviceBinding(role_id=rol.id, device_id=lamp.id))
        kijker = (await db.scalars(select(User).where(User.username == "kijker"))).one()
        db.add(ApiClient(user_id=kijker.id, role="viewer", purpose="phone", name="Leestelefoon",
                         token_hash=token_hash(telefoon), expires_at=datetime.now(UTC) + timedelta(days=10)))
        db.add_all([meting(lamp.id, 11), meting(lamp.id, 22)])
        await db.commit()
        return admin, pw, telefoon, lamp.id


@pytest.mark.parametrize("mode", ["same_home", "new_home"])
async def test_cli_drie_poorten(mode, tmp_path, monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "backup_dir", tmp_path / "archieven")
    monkeypatch.setenv("SHC_BACKUP_PASSPHRASE", PW)
    async with proefinstallatie(monkeypatch) as admin:
        assert (await admin.post("/homes", {"name": "Bronwaarde"})).is_success
        rec = await backup.run_backup(PW)
        bestand = cfg.backup_dir / rec["filename"]
        async with sessionmaker()() as db:
            (await db.scalars(select(Home))).one().name = "Doelwaarde"
            await db.commit()
        args = cli.build_parser().parse_args(["restore", str(bestand), "--mode", mode, "--yes"])

        async def voer_uit():
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                code = await cli._restore(args, dry_run=False)
            return code, stdout.getvalue()

        code, tekst = await voer_uit()
        assert code == 3 and "Bronwaarde" in tekst and "Doelwaarde" in tekst
        sleutel, akkoord = re.search(r"--rollback-id ([\w-]+) --confirmation ([a-f0-9]{64})", tekst).groups()
        code, tweede = await voer_uit()
        assert code == 3
        ander_akkoord = re.search(r"--confirmation ([a-f0-9]{64})", tweede)[1]
        args.rollback_id, args.confirmation = uuid.UUID(sleutel), ander_akkoord
        assert (await voer_uit())[0] == 2
        args.confirmation = akkoord
        archief = cfg.backup_dir / "terugval" / (sleutel + ".shcbak")
        assert backup.verify_file(archief, PW).summary["homes"][0]["name"] == "Doelwaarde"
        for onderdeel in (archief, archief.with_suffix(".json")):
            tijdelijk = onderdeel.with_suffix(".apart")
            onderdeel.rename(tijdelijk)
            try:
                assert (await voer_uit())[0] == 2
            finally:
                tijdelijk.rename(onderdeel)
        args.confirmation = None
        assert (await voer_uit())[0] == 2
        assert await _scalar(cfg.database_url, "SELECT name FROM homes") == "Doelwaarde"
        args.confirmation = akkoord
        assert (await voer_uit())[0] == 0
        assert await _scalar(cfg.database_url, "SELECT count(*) FROM homes WHERE name='Bronwaarde'") == 1
        async with temp_db() as terugval:
            await backup.restore_file(archief, PW, url=terugval)
            assert await _scalar(terugval, "SELECT name FROM homes") == "Doelwaarde"
        print(f"CLI {mode}: vergelijking 3; zonder doelkopie 2, vergelijkingsrecord 2, akkoord 2, ander geldig akkoord 2; met eigen akkoord 0; doelkopie terugleesbaar.")


async def test_documentair_dumpherstel(tmp_path, monkeypatch):
    """Volg de normale gedocumenteerde dumpimport uitsluitend op onze twee tijdelijke databases."""
    cfg = get_settings()
    monkeypatch.setattr(cfg, "backup_dir", tmp_path)
    async with proefinstallatie(monkeypatch) as bron:
        assert (await bron.post("/homes", {"name": "Bronwoning"})).is_success
        dump = await backup.run_db_dump(PW)
        archief = tmp_path / dump["filename"]
        monkeypatch.setenv("SHC_BACKUP_PASSPHRASE", PW)
        doc = (REPO / "docs/onderhoud.md").read_text()
        instructions = doc.split("### Database-dump terugzetten")[1].split("### ")[0]
        line, = [line for line in instructions.splitlines() if " shc-api restore-dump " in line]
        command = shlex.split(line)[4:]
        assert command == ["shc-api", "restore-dump", "/backups/<bestand>.shcdump", "--new-database", "herstelproef"]
        assert "--clean" not in instructions
        async with proefinstallatie(monkeypatch) as doel:
            assert (await doel.post("/homes", {"name": "Doelwoning"})).is_success
            url = cfg.database_url
            command[2] = str(archief)

            async def importeer():
                proc = await asyncio.create_subprocess_exec(*command, env=dict(os.environ, SHC_DATABASE_URL=url),
                                                            stdout=asyncio.subprocess.DEVNULL,
                                                            stderr=asyncio.subprocess.DEVNULL)
                return await proc.wait()

            command[-1] = url.rsplit("/", 1)[1]
            assert command[-1].startswith("shc_tmp_")
            assert await importeer() == 2
            assert await _scalar(url, "SELECT name FROM homes") == "Doelwoning"
            assert not list(tmp_path.rglob("*.shcbak"))
            command[-1] = "shc_tmp_" + uuid.uuid4().hex
            admin = await asyncpg.connect(_admin_dsn())
            try:
                assert await importeer() == 0
                fresh = url.rsplit("/", 1)[0] + "/" + command[-1]
                assert await _scalar(fresh, "SELECT name FROM homes") == "Bronwoning"
                assert await importeer() == 2
                assert await _scalar(url, "SELECT name FROM homes") == "Doelwoning"
            finally:
                await admin.execute(f'DROP DATABASE IF EXISTS "{command[-1]}"')
                await admin.close()


async def test_documentaire_sql_proefdatabase(tmp_path, monkeypatch):
    # Voer de operatoropdracht uit met de server en de namen van deze geïsoleerde proef.
    doc = (REPO / "docs/onderhoud.md").read_text()
    snippet, = re.findall(r"`(createdb [^`]+)`", doc)
    assert snippet == ("createdb --template=template0 herstelproef && psql -X --single-transaction "
                       "-v ON_ERROR_STOP=1 -d herstelproef -f <nieuw.sql>")
    parts = urlsplit(_admin_dsn())
    env = dict(os.environ, PGUSER=parts.username or "postgres")
    if parts.hostname:
        env["PGHOST"] = parts.hostname
    if parts.port:
        env["PGPORT"] = str(parts.port)
    if parts.password:
        env["PGPASSWORD"] = parts.password
    async with proefinstallatie(monkeypatch) as bron:
        assert (await bron.post("/homes", {"name": "SQL-proefwoning"})).is_success
        url = get_settings().database_url
        dump = await asyncio.to_thread(cli.pre_migration_dump, url, backup.head_revision(), tmp_path)
        sql = tmp_path / "gecontroleerd.sql"
        cli.verify_migration_dump(dump, sql)

        async def importeer(naam):
            command = snippet.replace("herstelproef", shlex.quote(naam)).replace("<nieuw.sql>", shlex.quote(str(sql)))
            proc = await asyncio.create_subprocess_exec("sh", "-c", command, env=env,
                                                        stdout=asyncio.subprocess.DEVNULL,
                                                        stderr=asyncio.subprocess.DEVNULL)
            return await proc.wait()

        naam = url.rsplit("/", 1)[1]
        assert naam.startswith("shc_tmp_") and await importeer(naam) != 0
        assert await _scalar(url, "SELECT name FROM homes") == "SQL-proefwoning"
        nieuw = "shc_tmp_" + uuid.uuid4().hex
        admin = await asyncpg.connect(_admin_dsn())
        try:
            assert await importeer(nieuw) == 0
            assert await _scalar(url.rsplit("/", 1)[0] + "/" + nieuw, "SELECT name FROM homes") == "SQL-proefwoning"
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{nieuw}"')
            await admin.close()


async def test_leestoegang_bronpauze(tmp_path, monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "data_dir", tmp_path / "app")
    zmap = tmp_path / "zigbee2mqtt"
    zmap.mkdir()
    monkeypatch.setattr(cfg, "zigbee2mqtt_dir", zmap)
    async with proefinstallatie(monkeypatch) as admin:
        assert (await admin.post("/homes", {"name": "Proefwoning"})).is_success
        pw = secrets.token_urlsafe(24)
        assert (await admin.post("/auth/users", {"username": "lezer", "role": "viewer", "password": pw})).is_success
        telefoon = new_token()
        async with sessionmaker()() as db:
            uid = (await db.scalars(select(User.id).where(User.role == "admin"))).one()
            telefoonrij = ApiClient(user_id=uid, role="control", purpose="phone", name="Proeftelefoon",
                                    token_hash=token_hash(telefoon), last_used_at=datetime.now(UTC) - timedelta(minutes=12),
                                    expires_at=datetime.now(UTC) + timedelta(days=2))
            db.add(telefoonrij)
            await db.commit()
            cid = telefoonrij.id
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as lezer:
            headers = {"Authorization": "Bearer " + telefoon}
            voor = await lezer.get("/api/v1/homes", headers=headers)
            assert voor.status_code == 200
            assert (await lezer.post("/api/v1/auth/login", json={"username": "lezer", "password": pw})).is_success
            async with sessionmaker()() as db:
                (await db.get(ApiClient, cid)).last_used_at = datetime.now(UTC) - timedelta(minutes=10)
                await db.commit()
            assert (await admin.post("/system/restore/source/pause")).status_code == 200
            cookie = await lezer.get("/api/v1/homes")
            tel = await lezer.get("/api/v1/homes", headers=headers)
            lezer.cookies.clear()
            assert (await lezer.get("/api/v1/homes")).status_code == 401
            assert (await lezer.post("/api/v1/auth/login", json={"username": "lezer", "password": "fout"})).status_code == 401
            login = await lezer.post("/api/v1/auth/login", json={"username": "lezer", "password": pw})
            print(f"Bronlezers: bestaande browsersessie {cookie.status_code}; gekoppelde telefoon vóór {voor.status_code}, tijdens {tel.status_code}; nieuwe leessessie {login.status_code}.")
            assert (await lezer.get("/api/v1/homes")).json()[0]["name"] == "Proefwoning"
            assert (await admin.post("/homes", {"name": "Late wijziging"})).status_code == 409
            assert cookie.status_code == tel.status_code == login.status_code == 200, "De bron moet voor bestaande gebruikers en gekoppelde clients leesbaar blijven."


@pytest.mark.parametrize("geval", ["terugval", "zigbee_na_export", "doelretentie", "knopfunctie", "inbedrijfstelling"])
async def test_eindvergelijking(geval, tmp_path, monkeypatch):
    from .ws_devices_support import make_worker

    cfg = get_settings()
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0)
    transport = httpx.ASGITransport(app=create_app())
    async with temp_db() as bron, temp_db() as doel:
        try:
            async def locatie(naam, url):
                await dispose()
                mapje = tmp_path / naam
                (mapje / "app").mkdir(parents=True, exist_ok=True)
                zigbee = mapje / "zigbee2mqtt"
                zigbee.mkdir(exist_ok=True)
                monkeypatch.setattr(cfg, "database_url", url)
                monkeypatch.setattr(cfg, "data_dir", mapje / "app")
                monkeypatch.setattr(cfg, "zigbee2mqtt_dir", zigbee)
                monkeypatch.setattr(cfg, "backup_dir", mapje / "backups")
                return zigbee

            zigbee = await locatie("bron", bron)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                admin, pw, _, lamp_id = await richt_bron_in(c)
                if geval == "knopfunctie":
                    async with sessionmaker()() as db:
                        hid = (await db.get(Device, lamp_id)).home_id
                        knop = Device(home_id=hid, provider="hue", kind="button", name="Proefknop",
                                      stable_identity=uuid.uuid4().hex, capabilities=[])
                        db.add(knop)
                        await db.flush()
                        mapping = ButtonMapping(home_id=hid, device_id=knop.id, button="1", event="short_release",
                                                mode="lights", action={"steps": [{"type": "light", "device_id": str(lamp_id),
                                                                                  "action": "toggle"}]})
                        db.add(mapping)
                        await db.commit()
                        mapping_id = mapping.id
                        knop_id = knop.id
                    gekoppeld = await admin.get("/buttons/" + str(knop_id))
                    assert gekoppeld.status_code == 200
                    assert any(str(m["id"]) == str(mapping_id) and m["enabled"]
                               and m["action"]["steps"][0]["action"] == "toggle" for m in gekoppeld.json()["mapping"])
                if geval == "doelretentie":
                    async with sessionmaker()() as db:
                        await db.execute(text("UPDATE sensor_readings SET metric='power_w', observed_at=now()-interval '10 days' WHERE value=11"))
                        await db.commit()
                pauze = await admin.post("/system/restore/source/pause")
                assert pauze.status_code == 200 and pauze.json()["reading_count"] == 2
                antwoord = await admin.post("/system/restore/source/export", {"passphrase": PW, "stop_source": True})
                assert antwoord.status_code == 200
                inhoud = (await admin.get("/system/restore/files/" + antwoord.json()["file_id"])).content
                if geval == "zigbee_na_export":
                    # De fysieke opslag weigert een onafhankelijke schrijver, ook als de API is herstart.
                    with pytest.raises(PermissionError):
                        (zigbee / "database.db").write_text("synthetische apparaatstand")
                    assert not list(zigbee.iterdir())
            doelzigbee = await locatie("doel", doel)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                setup = await c.post("/api/v1/auth/setup", json={"username": "tijdelijk", "password": pw})
                assert setup.status_code == 200
                admin = Session(c, setup.json()["csrf_token"])
                bestand = (await _upload(admin, inhoud, PW)).json()
                basis = {"upload_id": bestand["upload_id"], "passphrase": PW}
                proef = (await admin.post("/system/restore/drill", basis)).json()
                assert proef["data_ok"] and proef["drill_id"]
                voorstel = await reviewed(admin, {**basis, "mode": "migration", "drill_id": proef["drill_id"]})
                if geval == "terugval":
                    ander = await reviewed(admin, {**basis, "mode": "migration", "drill_id": proef["drill_id"]})
                    onjuist = await admin.post("/system/restore/apply", {**voorstel, "confirmation": ander["confirmation"]})
                    assert onjuist.status_code == 409
                kopie_id = voorstel["rollback_id"]
                kopie = (await admin.get("/system/restore/files/" + kopie_id)).content
                assert await status_taak(admin, await admin.post("/system/restore/apply", voorstel)) == "done"
                aanmelding = await c.post("/api/v1/auth/login", json={"username": "beheer", "password": pw})
                assert aanmelding.status_code == 200
                admin = Session(c, aanmelding.json()["csrf_token"])
                if geval == "terugval":
                    vreemd = (await _upload(admin, inhoud, PW)).json()
                    verkeerd = await reviewed(admin, {"upload_id": vreemd["upload_id"], "passphrase": PW, "mode": "same_home"})
                    geweigerd = await admin.post("/system/restore/apply", verkeerd)
                    assert geweigerd.status_code == 409 and geweigerd.json()["detail"]["code"] == "migration_pending"
                    upload = (await _upload(admin, kopie, PW)).json()
                    akkoord = await verify_three_gates(admin, {"upload_id": upload["upload_id"], "passphrase": PW, "mode": "same_home"})
                    assert await status_taak(admin, await admin.post("/system/restore/apply", akkoord)) == "done"
                    assert await _scalar(doel, "SELECT array_agg(username ORDER BY username) FROM users") == ["tijdelijk"]
                    assert await _scalar(bron, "SELECT count(*) FROM sensor_readings") == 2
                    login = await c.post("/api/v1/auth/login", json={"username": "tijdelijk", "password": pw})
                    assert login.status_code == 200
                    print("Open migratie terugzetten: andere export 409; zonder doelkopie/vergelijking/akkoord ieder 409; juiste doelkopie 202, taak done, oorspronkelijke beheerder logt in.")
                    return
                # Bij de eerste vergelijking heeft de doelsimulator het apparaat nog niet gemeld.
                eerste = (await admin.get("/system/restore/compare")).json()
                assert eerste["migration"]["confirmation"] is None
                async with sessionmaker()() as db:
                    for apparaat in await db.scalars(select(Device)):
                        apparaat.last_seen, apparaat.online = datetime.now(UTC), True
                    await db.commit()
                offered = (await admin.get("/system/restore/compare")).json()["migration"]["confirmation"]
                assert offered
                if geval == "knopfunctie":
                    assert await _scalar(doel, "SELECT count(*) FROM button_mappings") == 1
                    for key, replacement in (
                        ("enabled", False), ("button", "2"), ("event", "hold"),
                        ("action", {"steps": [{"type": "light", "device_id": str(knop_id), "action": "toggle"}]}),
                        ("action", {"steps": [{"type": "light", "device_id": str(lamp_id), "action": "off"}]}),
                    ):
                        async with sessionmaker()() as db:
                            row = await db.get(ButtonMapping, mapping_id)
                            original = getattr(row, key)
                            setattr(row, key, replacement)
                            await db.commit()
                        changed = (await admin.get("/system/restore/compare")).json()
                        assert changed["inventory"]["button_mappings"] == {"source": 1, "target": 1, "same": False}
                        assert changed["migration"]["confirmation"] is None and changed["summary"]["unavailable"] > 0
                        assert (await admin.post("/system/restore/confirm", {"source_stopped": True, "confirmation": offered})).status_code == 409
                        async with sessionmaker()() as db:
                            setattr(await db.get(ButtonMapping, mapping_id), key, original)
                            await db.commit()
                    assert (await admin.delete("/button-mappings/" + str(mapping_id))).status_code == 204
                    assert await _scalar(bron, "SELECT count(*) FROM button_mappings") == 1
                    assert await _scalar(doel, "SELECT count(*) FROM button_mappings") == 0
                if geval == "doelretentie":
                    monkeypatch.setattr(cfg, "retention_power_days", 5)
                    assert (await make_worker().retention_round())["power"] == 0
                    assert await _scalar(doel, "SELECT count(*) FROM sensor_readings") == 2
                    # Ook schade met gelijkblijvend aantal en laatste ID moet vrijgave blokkeren.
                    async with sessionmaker()() as db:
                        await db.execute(text("UPDATE sensor_readings SET value=33 WHERE value=11"))
                        await db.commit()
                if geval == "inbedrijfstelling":
                    async with sessionmaker()() as db:
                        await set_setting(db, "commissioning_gate", {})
                        await db.commit()
                bron_n = await _scalar(bron, "SELECT count(*) FROM sensor_readings")
                doel_n = await _scalar(doel, "SELECT count(*) FROM sensor_readings")
                vergelijking = (await admin.get("/system/restore/compare")).json()
                handtekening = vergelijking["migration"]["confirmation"]
                vrijgave = None
                if handtekening:
                    reactie = await admin.post("/system/restore/confirm", {"source_stopped": True, "confirmation": handtekening})
                    vrijgave = reactie.status_code
                print(f"{geval}: bronmetingen {bron_n}, doelmetingen {doel_n}, Zigbee-bestanden bron {len(list(zigbee.iterdir()))}, doel {len(list(doelzigbee.iterdir()))}, akkoord aangeboden {bool(handtekening)}, vrijgave HTTP {vrijgave}.")
                aangeboden = bool(handtekening)
                if geval == "knopfunctie":
                    print(f"Knopkoppeling: bron 1, doel 0, onbeschikbare functies {vergelijking['summary']['unavailable']}, vrijgave HTTP {vrijgave}.")
                if geval == "zigbee_na_export":
                    assert aangeboden and vrijgave == 200
                    assert not list(zigbee.iterdir()) and bron_n == doel_n == 2
                    return
                soort = {"doelretentie": "sensor_readings", "knopfunctie": "button_mappings",
                         "inbedrijfstelling": "commissioning_gate"}[geval]
                assert not vergelijking["inventory"][soort]["same"]
                assert (aangeboden, vrijgave == 200) == (False, False), "De actuele afwijking moet de overstap tegenhouden."
                stale = {"source_stopped": True, "confirmation": offered}
                assert (await admin.post("/system/restore/confirm", stale)).status_code == 409
                if geval == "doelretentie":
                    async with sessionmaker()() as db:
                        await db.execute(text("DELETE FROM sensor_readings WHERE value=33"))
                        await db.commit()
                    lost = (await admin.get("/system/restore/compare")).json()
                    assert lost["inventory"]["sensor_readings"] == {"source": 2, "target": 1, "same": False}
                    assert lost["migration"]["confirmation"] is None
                    assert (await admin.post("/system/restore/confirm", stale)).status_code == 409
        finally:
            await dispose()


CLI_HERSTART = """
import asyncio, json, os, sys
from shc_api import backup, cli
from shc_api.main import create_app
from shc_api.db import sessionmaker
from shc_api.restore_control import latest_job
from shc_api.store import get_setting
from sqlalchemy import text

async def run():
    opt = json.load(sys.stdin)
    if opt['fase'] == 'herstart':
        app = create_app()
        async with app.router.lifespan_context(app):
            async with sessionmaker()() as db:
                waarden = [await get_setting(db, key, False) for key in ('maintenance', 'safe_mode')]
                woning = await db.scalar(text('SELECT name FROM homes LIMIT 1'))
                print(json.dumps([*waarden, woning, await latest_job(db)]), flush=True)
        return
    if opt['fase'] == 'voor_commit':
        origineel = backup._after_restore
        def breek_af(*args):
            origineel(*args)
            os._exit(73)
        backup._after_restore = breek_af
    elif opt['fase'] == 'antwoord_verloren':
        herstel = backup.restore_file
        async def na_commit(*args, **kw):
            await herstel(*args, **kw)
            raise OSError('Antwoord op vastgelegde transactie verloren.')
        backup.restore_file = na_commit
    else:
        async def breek_af():
            os._exit(73)
        cli.publish_status_now = breek_af
    args = cli.build_parser().parse_args(['restore', opt['bestand'], '--yes',
        '--rollback-id', opt['kopie'], '--confirmation', opt['akkoord']])
    code = await cli._restore(args, dry_run=False)
    if opt['fase'] == 'antwoord_verloren':
        sys.exit(code)
    raise AssertionError('Onderbrekingspunt niet bereikt.')
asyncio.run(run())
"""


@pytest.mark.parametrize(("fase", "vooraf_aan"), [
    ("voor_commit", False), ("voor_commit", True), ("na_commit", False), ("antwoord_verloren", False),
])
async def test_cli_herstart(fase, vooraf_aan, tmp_path, monkeypatch):
    cfg = get_settings()
    monkeypatch.setenv("SHC_BACKUP_PASSPHRASE", PW)
    monkeypatch.setattr(cfg, "backup_dir", tmp_path)
    async with proefinstallatie(monkeypatch) as admin:
        assert (await admin.post("/homes", {"name": "Archiefwoning"})).is_success
        kopie = await backup.run_backup(PW)
        bestand = tmp_path / kopie["filename"]
        async with sessionmaker()() as db:
            (await db.scalars(select(Home))).one().name = "Doelwoning"
            await set_setting(db, "maintenance", vooraf_aan)
            await set_setting(db, "safe_mode", vooraf_aan)
            await db.commit()
        args = cli.build_parser().parse_args(["restore", str(bestand), "--yes"])
        antwoord = io.StringIO()
        with contextlib.redirect_stdout(antwoord):
            assert await cli._restore(args, dry_run=False) == 3
        kopie_id, akkoord = re.search(r"--rollback-id ([\w-]+) --confirmation ([a-f0-9]{64})", antwoord.getvalue()).groups()
        omgeving = dict(os.environ, SHC_DATABASE_URL=cfg.database_url, SHC_BACKUP_DIR=str(tmp_path),
                        SHC_DATA_DIR=str(tmp_path / "app"))

        async def kind(stand):
            p = await asyncio.create_subprocess_exec(sys.executable, "-c", CLI_HERSTART, env=omgeving,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            bericht = json.dumps({"fase": stand, "bestand": str(bestand), "kopie": kopie_id, "akkoord": akkoord}).encode()
            out, _ = await p.communicate(bericht)
            return p.returncode, out
        assert (await kind(fase))[0] == (0 if fase == "antwoord_verloren" else 73)
        code, gegevens = await kind("herstart")
        assert code == 0
        onderhoud, veilig, woning, taak = json.loads(gegevens.splitlines()[-1])
        print(f"CLI {fase}: na herstart woning {woning}, onderhoud {onderhoud}, veilig {veilig}, stap {taak['step']}.")
        assert woning == ("Doelwoning" if fase == "voor_commit" else "Archiefwoning")
        assert onderhoud == (vooraf_aan and fase == "voor_commit")
        assert veilig == (vooraf_aan or fase != "voor_commit")
        assert (taak["state"], taak["step"]) == (("failed", "onderbroken") if fase == "voor_commit" else ("done", "teruggezet"))



async def test_api_herstart_in_transactie(tmp_path, monkeypatch):
    async with proefinstallatie(monkeypatch) as bron:
        assert (await bron.post("/homes", {"name": "Bron voor transactieproef"})).is_success
        record = await backup.run_backup(PW, backup_dir=tmp_path)
        async with temp_db() as url:
            omgeving = dict(os.environ, R2_TESTDB=url, SHC_BACKUP_DIR=str(tmp_path / "taak"),
                            SHC_DATA_DIR=str(tmp_path / "app"))
            code = """
import asyncio, os
from tests.test_herstelpoorten import _process
from shc_api import backup
from shc_api.config import get_settings
os.environ['SHC_DATABASE_URL'] = os.environ['R2_TESTDB']
get_settings.cache_clear()
origineel = backup._after_restore
def voor_vastleggen(*args):
    origineel(*args)
    os._exit(73)
backup._after_restore = voor_vastleggen
asyncio.run(_process())
"""

            async def kind(fase, **extra):
                bericht = dict(phase=fase, archive=str(tmp_path / record["filename"]),
                               password=secrets.token_urlsafe(32), **extra)
                p = await asyncio.create_subprocess_exec(sys.executable, "-c", code, env=omgeving,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                uit, _ = await p.communicate(json.dumps(bericht).encode())
                return p.returncode, json.loads(uit.splitlines()[-1])
            exitcode, taak_id = await kind("after")
            assert exitcode == 73
            exitcode, (staat, (onderhoud, veilig, herkomst), _, huizen) = await kind("restart", job_id=taak_id)
            assert exitcode == 0 and staat == "failed" and not onderhoud and not veilig and not herkomst
            assert huizen == ["Herstartproef doelwoning"]
            print("API tijdens transactie: taak failed, oorspronkelijke doelwoning aanwezig, onderhoud uit, veilig uit, geen nieuw herkomstrapport.")


async def test_eerste_geldige_vergelijking(tmp_path, monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0)
    pw = secrets.token_urlsafe(24)
    async with proefinstallatie(monkeypatch) as bron:
        bronmap = tmp_path / "bron"
        (bronmap / "app").mkdir(parents=True)
        (bronmap / "zigbee2mqtt").mkdir()
        monkeypatch.setattr(cfg, "data_dir", bronmap / "app")
        monkeypatch.setattr(cfg, "zigbee2mqtt_dir", bronmap / "zigbee2mqtt")
        monkeypatch.setattr(cfg, "backup_dir", bronmap / "backups")
        assert (await bron.post("/homes", {"name": "Overeenkomende woning"})).is_success
        assert (await bron.post("/auth/users", {"username": "bronbeheer", "role": "admin", "password": pw})).is_success
        assert (await bron.post("/system/restore/source/pause")).is_success
        bestand = (await bron.post("/system/restore/source/export", {"passphrase": PW, "stop_source": True})).json()
        inhoud = (await bron.get("/system/restore/files/" + bestand["file_id"])).content
        async with proefinstallatie(monkeypatch) as doel:
            doelmap = tmp_path / "doel"
            (doelmap / "app").mkdir(parents=True)
            (doelmap / "zigbee2mqtt").mkdir()
            monkeypatch.setattr(cfg, "data_dir", doelmap / "app")
            monkeypatch.setattr(cfg, "zigbee2mqtt_dir", doelmap / "zigbee2mqtt")
            monkeypatch.setattr(cfg, "backup_dir", doelmap / "backups")
            upload = (await _upload(doel, inhoud, PW)).json()
            basis = {"upload_id": upload["upload_id"], "passphrase": PW}
            proef = (await doel.post("/system/restore/drill", basis)).json()
            assert proef["data_ok"] and proef["drill_id"]
            akkoord = await reviewed(doel, {**basis, "mode": "migration", "drill_id": proef["drill_id"]})
            assert await status_taak(doel, await doel.post("/system/restore/apply", akkoord)) == "done"
            login = await doel.c.post("/api/v1/auth/login", json={"username": "bronbeheer", "password": pw})
            assert login.is_success
            admin = Session(doel.c, login.json()["csrf_token"])

            async def bevestig():
                rapport = (await admin.get("/system/restore/compare")).json()
                assert rapport["source_homes"] == rapport["homes"]
                assert rapport["summary"]["unavailable"] == rapport["summary"]["partial"] == 0
                assert rapport["missing_devices"] == 0 and rapport["migration"]["source_complete"]
                assert bool(rapport["migration"]["confirmation"])
                antwoord = await admin.post("/system/restore/confirm", {
                    "source_stopped": True, "confirmation": rapport["migration"]["confirmation"]})
                return antwoord.status_code
            eerste = await bevestig()
            tweede = await bevestig() if eerste == 409 else None
            print(f"Ongewijzigd doel: eerste aangeboden akkoord HTTP {eerste}, na opnieuw vergelijken HTTP {tweede}.")
            assert eerste == 200
