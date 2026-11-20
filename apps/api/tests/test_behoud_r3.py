"""De vier tegenproeven met een afzonderlijke bron en doel en uitsluitend testgegevens."""
import asyncio
import secrets
import stat
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select, text

from shc_api import backup, migration_source
from shc_api.config import get_settings
from shc_api.db import dispose, sessionmaker
from shc_api.main import create_app
from shc_api.models import Device
from shc_api.routers import auth, system
from shc_api.store import set_setting

from .conftest import Session
from .restore_approval import reviewed
from .test_herreview import richt_bron_in, status_taak
from .test_herstelpoorten import proefinstallatie
from .test_ws_backup_api import _upload
from .test_ws_backup_format import PW, temp_db
from .test_ws_backup_restore import _scalar


def stel_opslag_in(monkeypatch, pad):
    cfg = get_settings()
    app = pad / "app"
    zigbee = pad / "zigbee2mqtt"
    app.mkdir(parents=True)
    zigbee.mkdir()
    monkeypatch.setattr(cfg, "data_dir", app)
    monkeypatch.setattr(cfg, "backup_dir", pad / "backups")
    monkeypatch.setattr(cfg, "zigbee2mqtt_dir", zigbee)
    return zigbee


async def kijker_meldt_aan(transport, zin):
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as kijker:
        login = await kijker.post("/api/v1/auth/login", json={"username": "kijker", "password": zin})
        assert login.status_code == 200
        assert (await kijker.get("/api/v1/homes")).status_code == 200


async def bronkeuzes(beheer, lamp):
    async with sessionmaker()() as db:
        woning = (await db.get(Device, lamp)).home_id
        oud = Device(home_id=woning, provider="hue", kind="light_group", name="Oude proeflamp",
                     stable_identity=uuid.uuid4().hex, capabilities=["power"])
        db.add(oud)
        await db.flush()
        oud_id = oud.id
        await db.commit()
    vervang = await beheer.post(f"/devices/{oud_id}/replace", {"new_device_id": str(lamp)})
    assert vervang.status_code == 200
    bevestigd = await beheer.post(f"/devices/{lamp}/tested", {"note": "Bevestigd in de proefwoning"})
    assert bevestigd.status_code == 200
    return oud_id


async def keuzes_lezen(beheer, oud, lamp):
    async def diagnose(identiteit):
        antwoord = await beheer.get(f"/devices/{identiteit}/diagnostics")
        assert antwoord.status_code == 200
        return antwoord.json()["diagnostics"]["device"]["diagnostics"]
    opvolger = (await diagnose(oud)).get("replaced_by") == str(lamp)
    notitie = (await diagnose(lamp)).get("tested", {}).get("note") == "Bevestigd in de proefwoning"
    return opvolger, notitie


@pytest.mark.parametrize("geval", ["opvolger", "beheernotitie", "aanmeldhistorie"])
async def test_vrijgave_met_gevulde_bron(geval, tmp_path, monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(system, "MAINTENANCE_SETTLE_S", 0)
    transport = httpx.ASGITransport(app=create_app())
    async with temp_db() as bron, temp_db() as doel:
        try:
            await dispose()
            monkeypatch.setattr(cfg, "database_url", bron)
            stel_opslag_in(monkeypatch, tmp_path / "bron")
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                beheer, zin, _, lamp = await richt_bron_in(c)
                oud = await bronkeuzes(beheer, lamp)
                assert await keuzes_lezen(beheer, oud, lamp) == (True, True)
                assert (await beheer.post("/system/restore/source/pause")).status_code == 200
                if geval == "aanmeldhistorie":
                    await kijker_meldt_aan(transport, zin)
                    tussen = await beheer.post("/system/restore/source/export", {"passphrase": PW})
                    assert tussen.status_code == 200
                    tussenbestand = (await beheer.get("/system/restore/files/" + tussen.json()["file_id"])).content
                    await kijker_meldt_aan(transport, zin)
                    async with httpx.AsyncClient(transport=transport, base_url="http://test") as kijker:
                        fout = await kijker.post("/api/v1/auth/login", json={"username": "kijker", "password": "fout"})
                        assert fout.status_code == 401
                eindexport = {"passphrase": PW, "stop_source": True}
                antwoord = await beheer.post("/system/restore/source/export", eindexport)
                assert antwoord.status_code == 200
                if geval == "aanmeldhistorie":
                    herhaling = await beheer.post("/system/restore/source/export", eindexport)
                    assert herhaling.status_code == 200
                    assert herhaling.json()["source_pause"]["stopped_at"] == antwoord.json()["source_pause"]["stopped_at"]
                    antwoord = herhaling
                    geweigerd = await c.post("/api/v1/auth/login", json={"username": "kijker", "password": zin})
                    assert geweigerd.status_code == 409
                    grens = await _scalar(bron, "SELECT max(id) FROM audit_log")
                    historie = "SELECT jsonb_agg(to_jsonb(a) ORDER BY id) FROM audit_log a WHERE id <= :grens"
                    bronregels = await _scalar(bron, historie, grens=grens)
                    assert [r["action"] for r in bronregels if r["actor"] == "kijker"] == ["login", "login", "login_failed"]
                    assert sum(r["action"] == "migration_source_stopped" for r in bronregels) == 1
                    assert await _scalar(bron, "SELECT count(*) FROM shc_pause.events") == 0
                download = await beheer.get("/system/restore/files/" + antwoord.json()["file_id"])
                assert download.status_code == 200

            await dispose()
            monkeypatch.setattr(cfg, "database_url", doel)
            stel_opslag_in(monkeypatch, tmp_path / "doel")
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                setup = await c.post("/api/v1/auth/setup", json={"username": "doel", "password": zin})
                assert setup.status_code == 200
                beheer = Session(c, setup.json()["csrf_token"])
                if geval == "aanmeldhistorie":
                    tussen = await _upload(beheer, tussenbestand, PW)
                    basis = {"upload_id": tussen.json()["upload_id"], "passphrase": PW}
                    proef = (await beheer.post("/system/restore/drill", basis)).json()
                    assert proef["data_ok"] and not proef["drill_id"]
                    assert (await beheer.post("/system/restore/apply", {**basis, "mode": "migration"})).status_code == 409
                upload = await _upload(beheer, download.content, PW)
                assert upload.status_code == 200
                aanvraag = {"upload_id": upload.json()["upload_id"], "passphrase": PW}
                herstelproef = await beheer.post("/system/restore/drill", aanvraag)
                assert herstelproef.status_code == 200 and herstelproef.json()["data_ok"] and herstelproef.json()["drill_id"]
                voorstel = await reviewed(beheer, {**aanvraag, "mode": "migration", "drill_id": herstelproef.json()["drill_id"]})
                assert await status_taak(beheer, await beheer.post("/system/restore/apply", voorstel)) == "done"
                login = await c.post("/api/v1/auth/login", json={"username": "beheer", "password": zin})
                assert login.status_code == 200
                beheer = Session(c, login.json()["csrf_token"])
                assert await keuzes_lezen(beheer, oud, lamp) == (True, True)

                async with sessionmaker()() as db:
                    for apparaat in await db.scalars(select(Device)):
                        apparaat.online = True
                        apparaat.last_seen = datetime.now(UTC)
                    await db.commit()
                eerste = (await beheer.get("/system/restore/compare")).json()
                assert eerste["migration"]["confirmation"]
                if geval == "aanmeldhistorie":
                    assert await _scalar(doel, historie, grens=grens) == bronregels
                    assert await _scalar(doel, "SELECT count(*) FROM audit_log WHERE actor='kijker' AND action='login'") == 2
                    assert await _scalar(doel, "SELECT failed_logins FROM users WHERE username='kijker'") == 1
                    assert eerste["inventory"]["audit_log"] == {"source": len(bronregels), "target": len(bronregels), "same": True}
                    async with sessionmaker()() as db:
                        dubbel = await db.scalar(text("""INSERT INTO audit_log (at,actor,action,target,detail)
                            SELECT at,actor,action,target,detail FROM audit_log WHERE actor='kijker' AND action='login'
                            ORDER BY id LIMIT 1 RETURNING id"""))
                        await db.commit()
                    akkoord = {"source_stopped": True, "confirmation": eerste["migration"]["confirmation"]}
                    assert (await beheer.post("/system/restore/confirm", akkoord)).status_code == 409
                    verschil = (await beheer.get("/system/restore/compare")).json()
                    assert not verschil["inventory"]["audit_log"]["same"] and not verschil["migration"]["confirmation"]
                    async with sessionmaker()() as db:
                        await db.execute(text("DELETE FROM audit_log WHERE id=:id"), {"id": dubbel})
                        await db.commit()
                    akkoord["confirmation"] = (await beheer.get("/system/restore/compare")).json()["migration"]["confirmation"]
                    assert (await beheer.post("/system/restore/confirm", akkoord)).status_code == 200
                    print("Aanmeldhistorie: twee aanmeldingen en één mislukte poging exact teruggelezen; dubbel 409, vrijgave 200.")
                    return
                else:
                    # Bootst één ontbrekend gebruikersveld op de eigen doeldatabase na.
                    # De normale beheerroute heeft beide velden op de bron vastgelegd.
                    async with sessionmaker()() as db:
                        if geval == "opvolger":
                            apparaat = await db.get(Device, oud)
                            apparaat.diagnostics = {k: v for k, v in apparaat.diagnostics.items() if k != "replaced_by"}
                        else:
                            apparaat = await db.get(Device, lamp)
                            getest = {k: v for k, v in apparaat.diagnostics["tested"].items() if k != "note"}
                            apparaat.diagnostics = {**apparaat.diagnostics, "tested": getest}
                        await db.commit()
                    waarden = await keuzes_lezen(beheer, oud, lamp)
                    assert waarden == ((False, True) if geval == "opvolger" else (True, False))
                    tabel = "devices"
                    observatie = f"bron opvolger/notitie=True/True, doel={waarden[0]}/{waarden[1]}"
                vergelijking = (await beheer.get("/system/restore/compare")).json()
                inventaris = vergelijking["inventory"][tabel]
                akkoord = vergelijking["migration"]["confirmation"] or eerste["migration"]["confirmation"]
                if akkoord:
                    reactie = await beheer.post("/system/restore/confirm", {"source_stopped": True, "confirmation": akkoord})
                    vrijgave = reactie.status_code
                else:
                    assert (await beheer.get("/system/settings")).json()["safe_mode"]
                    vrijgave = "geen akkoord"
                print(f"{geval}: {observatie}, {tabel}={inventaris['source']}/{inventaris['target']}, "
                      f"gelijk={inventaris['same']}, vrijgave={vrijgave}", flush=True)
                assert not inventaris["same"] and not vergelijking["migration"]["confirmation"] and vrijgave == 409
        finally:
            await dispose()


@pytest.mark.parametrize("onderhoud", [False, True])
async def test_geweigerde_opslagcontrole_laat_bron_beschikbaar(onderhoud, tmp_path, monkeypatch):
    zigbee = stel_opslag_in(monkeypatch, tmp_path)
    zigbee.chmod(0o750)
    mode = stat.S_IMODE(zigbee.stat().st_mode)
    async with proefinstallatie(monkeypatch) as beheer:
        async with sessionmaker()() as db:
            await set_setting(db, "maintenance", onderhoud)
            await set_setting(db, "safe_mode", onderhoud)
            await db.commit()
        assert (await beheer.post("/homes", {"name": "Bronwoning"})).status_code == 200
        vooraf = (await beheer.get("/system/settings")).json()
        assert vooraf["maintenance"] == onderhoud and not vooraf["source_pause"]
        origineel = migration_source._store_state

        def gelijktijdig_standbestand(pad):
            toestand = origineel(pad)
            (pad / "database.db").write_text("synthetische Zigbee-stand")
            return toestand

        # De schrijver is klaar ná het lezen van de lege map en vóór het afsluiten van die map.
        monkeypatch.setattr(migration_source, "_store_state", gelijktijdig_standbestand)
        try:
            weigering = await beheer.post("/system/restore/source/pause")
            stand = (await beheer.get("/system/settings")).json()
            wijziging = await beheer.post("/homes", {"name": "Volgende proefwoning"})
            hervat = await beheer.post("/system/restore/source/resume")
            assert (zigbee / "database.db").read_text() == "synthetische Zigbee-stand"
            rechten_gelijk = stat.S_IMODE(zigbee.stat().st_mode) == mode
            print(f"Geweigerde bronpauze: HTTP {weigering.status_code}, onderhoud={stand['maintenance']}, "
                  f"pauze={bool(stand['source_pause'])}, maprechten hersteld={rechten_gelijk}, "
                  f"hervatten={hervat.status_code}, schrijven={wijziging.status_code}", flush=True)
            assert weigering.status_code == 409
            assert wijziging.status_code == 200 and rechten_gelijk and not stand["source_pause"]
            assert stand["maintenance"] == vooraf["maintenance"] and stand["safe_mode"] == vooraf["safe_mode"]
        finally:
            zigbee.chmod(mode)


async def test_sluitpunt_wacht_op_lopende_aanmelding_en_overleeft_herhaling(tmp_path, monkeypatch):
    stel_opslag_in(monkeypatch, tmp_path)
    async with proefinstallatie(monkeypatch) as beheer:
        zin = secrets.token_urlsafe(24)
        assert (await beheer.post("/auth/users", {"username": "kijker", "role": "viewer", "password": zin})).is_success
        assert (await beheer.post("/system/restore/source/pause")).is_success
        transport = httpx.ASGITransport(app=create_app())
        eind = {"stop_source": True, "passphrase": PW}
        # Een afgekeurd bestand mag noch het journaal verliezen noch de aanmeldroute sluiten.
        await kijker_meldt_aan(transport, zin)
        def onleesbaar(*args):
            raise backup.BackupError("Synthetische verificatiefout.")

        with monkeypatch.context() as m:
            m.setattr(backup, "verify_file", onleesbaar)
            assert (await beheer.post("/system/restore/source/export", eind)).status_code == 409
        assert (await beheer.get("/system/settings")).json()["source_pause"]["stopped_at"] is None
        assert not list((tmp_path / "backups").rglob("*.shcbak"))
        assert not list((tmp_path / "backups").rglob("*.partial"))

        gestart, export_wacht, verder = asyncio.Event(), asyncio.Event(), asyncio.Event()
        controle = auth.password_matches
        lock = migration_source.lock_commissioning

        async def controleer(*args, **kwargs):
            gestart.set()
            await verder.wait()
            return await controle(*args, **kwargs)

        async def wacht_op_aanmelding(db):
            export_wacht.set()
            await lock(db)

        monkeypatch.setattr(auth, "password_matches", controleer)
        monkeypatch.setattr(migration_source, "lock_commissioning", wacht_op_aanmelding)
        async with asyncio.timeout(30), asyncio.TaskGroup() as taken:
            taken.create_task(kijker_meldt_aan(transport, zin))
            await gestart.wait()
            afsluiting = taken.create_task(beheer.post("/system/restore/source/export", eind))
            await export_wacht.wait()
            verder.set()
        assert afsluiting.result().status_code == 200
        assert (await beheer.post("/system/restore/source/export", eind)).is_success
        url = get_settings().database_url
        assert await _scalar(url, "SELECT count(*) FROM audit_log WHERE actor='kijker' AND action='login'") == 2
        assert await _scalar(url, "SELECT count(*) FROM audit_log WHERE action='migration_source_stopped'") == 1


async def test_onbekend_journaalveld_blijft_bewaard(tmp_path, monkeypatch):
    stel_opslag_in(monkeypatch, tmp_path)
    async with proefinstallatie(monkeypatch) as beheer:
        assert (await beheer.post("/system/restore/source/pause")).is_success
        async with sessionmaker()() as db:
            await db.execute(text("ALTER TABLE shc_pause.events ADD COLUMN extra jsonb"))
            await db.execute(text("""INSERT INTO shc_pause.events (action, extra)
                                   VALUES ('proef', '{"bewaren": true}')"""))
            await db.commit()
        antwoord = await beheer.post("/system/restore/source/export", {"passphrase": PW, "stop_source": True})
        assert antwoord.status_code == 409
        assert (await beheer.get("/system/settings")).json()["source_pause"]["stopped_at"] is None
        assert await _scalar(get_settings().database_url, "SELECT extra FROM shc_pause.events") == {"bewaren": True}
