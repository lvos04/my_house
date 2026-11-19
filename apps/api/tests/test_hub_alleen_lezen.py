"""Lokale contractproeven voor het viewer-token dat de Hub via een koppelcode krijgt."""

import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, event, func, select

from shc_api import backup, deps, watch
from shc_api.config import get_settings
from shc_api.db import engine, sessionmaker
from shc_api.models import Alert, ApiClient, AuditLog
from shc_api.store import get_setting, set_setting

from .conftest import ADMIN_PW
from .test_ws_backup_format import PW
from .test_ws_followups import settings_restored

EIGEN_SCHRIJFROUTES = {
    ("POST", "/api/v1/clients/self/refresh"),
    ("DELETE", "/api/v1/clients/self"),
}


@pytest.fixture
async def koppel(admin, app, monkeypatch):
    monkeypatch.setattr(get_settings(), "secret_key", secrets.token_urlsafe(48))
    monkeypatch.setattr(get_settings(), "secret_key_file", None)
    async with sessionmaker()() as db:
        oud_adres = await get_setting(db, "lan_url")
        await set_setting(db, "lan_url", "http://testwoning.local")
        await db.commit()

    async def nieuw(client, rol="viewer", doel="hub"):
        body = {"role": rol}
        if doel == "hub":
            body["purpose"] = doel
        r = await admin.post("/pairing/codes", json=body)
        assert r.status_code == 200
        r = await client.post(
            "/api/v1/pairing/redeem", json={"code": r.json()["code"], "device_name": "Lokale Hub-proef"},
        )
        assert r.status_code == 200
        data = r.json()
        assert (data["client"]["role"], data["client"]["purpose"]) == (rol, doel)
        return {"Authorization": f"Bearer {data['token']}"}, uuid.UUID(data["client"]["id"])

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client, nieuw
    finally:
        async with sessionmaker()() as db:
            await set_setting(db, "lan_url", oud_adres)
            await db.commit()


async def _toestand(client_id, sleutel):
    async with sessionmaker()() as db:
        aantallen = [
            await db.scalar(select(func.count()).select_from(tabel)) for tabel in (Alert, ApiClient, AuditLog)
        ]
        meldingen = (
            await db.execute(select(Alert.id, Alert.detail, Alert.resolved_at).where(Alert.key == sleutel))
        ).all()
        client = await db.get(ApiClient, client_id)
        return aantallen, meldingen, (client.last_used_at, client.expires_at, client.revoked_at)


async def test_leesroutes_committen_niet_en_laten_meldingen_aan_de_statusbewaker(admin, koppel, monkeypatch):
    client, nieuw = koppel
    headers, client_id = await nieuw(client)
    woning = (await admin.post("/homes", json={"name": "Lokale woning voor de leesproef"})).json()["id"]
    sleutel = f"worker_heartbeat:{woning}"
    now = datetime.now(UTC)
    async with sessionmaker()() as db:
        bewaard = {
            key: await get_setting(db, key) for key in ("maintenance", "worker_heartbeat", watch.WATCH_KEY)
        }
        await set_setting(db, "maintenance", False)
        await set_setting(db, "worker_heartbeat", (now - timedelta(minutes=10)).isoformat())
        # Nieuwe woning terwijl de bewaker de uitval al kent.
        await set_setting(db, watch.WATCH_KEY, {"worker": "error", "worker_down": True})
        await db.commit()

    async def geen_push(_db):
        return None, None

    monkeypatch.setattr(watch.notify, "configured_url", geen_push)
    paden = (
        "/health", "/system/status", "/homes", "/system/settings", "/auth/status", "/auth/me",
        "/pairing/identity", "/clients/self",
        *(f"/homes/{woning}/{sub}" for sub in ("overview", "zones", "lights", "alerts", "devices")),
    )

    async def lees_zonder_mutatie():
        ervoor = await _toestand(client_id, sleutel)
        commits = []
        noteer_commit = commits.append
        event.listen(engine().sync_engine, "commit", noteer_commit)
        try:
            for pad in paden:
                response = await client.get(f"/api/v1{pad}", headers=headers)
                assert response.status_code == 200, pad
        finally:
            event.remove(engine().sync_engine, "commit", noteer_commit)
        assert await _toestand(client_id, sleutel) == ervoor
        assert commits == []
        return ervoor[1]

    async def bewakingsronde(tijd):
        async with sessionmaker()() as db:
            await watch.check_once(db, tijd)

    try:
        assert await lees_zonder_mutatie() == []
        await bewakingsronde(now)
        [(melding, detail, opgelost)] = await lees_zonder_mutatie()
        assert opgelost is None
        await bewakingsronde(now + timedelta(minutes=1))
        [(dezelfde, nieuw_detail, opgelost)] = await lees_zonder_mutatie()
        assert dezelfde == melding and nieuw_detail != detail and opgelost is None
        assert (await client.get(f"/api/v1/homes/{woning}/alerts", headers=headers)).json()[0]["id"] == str(melding)

        async with sessionmaker()() as db:
            await set_setting(db, "worker_heartbeat", datetime.now(UTC).isoformat())
            await db.commit()
        assert (await lees_zonder_mutatie())[0].resolved_at is None
        await bewakingsronde(datetime.now(UTC))
        [(dezelfde, _, opgelost)] = await lees_zonder_mutatie()
        assert dezelfde == melding and opgelost is not None
        assert (await client.get(f"/api/v1/homes/{woning}/alerts", headers=headers)).json() == []
    finally:
        async with sessionmaker()() as db:
            for key, value in bewaard.items():
                await set_setting(db, key, value)
            await db.execute(delete(Alert).where(Alert.key == sleutel))
            await db.commit()


async def test_alle_schrijfroutes_weigeren_het_hub_token(app, admin, koppel):
    client, nieuw = koppel
    headers, client_id = await nieuw(client)
    toestand = await _toestand(client_id, "geen-melding")
    for pad, operaties in app.openapi()["paths"].items():
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            if method.lower() not in operaties or (method, pad) in EIGEN_SCHRIJFROUTES:
                continue
            url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), pad)
            response = await client.request(method, url, headers=headers, json={})
            assert response.status_code == 403, (method, pad, response.status_code)
    response = await admin.c.post("/api/v1/auth/logout", headers={**admin.h(), **headers})
    assert response.status_code == 403
    assert await _toestand(client_id, "geen-melding") == toestand


async def test_hub_vernieuwt_en_trekt_alleen_de_eigen_koppeling_in(koppel):
    client, nieuw = koppel
    headers, client_id = await nieuw(client)
    andere_headers, andere_id = await nieuw(client, "control", "phone")
    andere_toestand = (await _toestand(andere_id, "geen-melding"))[2]
    response = await client.post("/api/v1/clients/self/refresh", headers=headers)
    assert response.status_code == 200
    vernieuwd = {"Authorization": f"Bearer {response.json()['token']}"}
    assert (await client.get("/api/v1/clients/self", headers=headers)).status_code == 401
    eigen = (await client.get("/api/v1/clients/self", headers=vernieuwd)).json()
    assert eigen["role"] == "viewer" and eigen["purpose"] == "hub" and eigen["id"] == str(client_id)
    assert (await client.post("/api/v1/auth/password", headers=vernieuwd, json={})).status_code == 403
    assert (await client.delete("/api/v1/clients/self", headers=vernieuwd)).status_code == 204
    geweigerd = await client.get("/api/v1/clients/self", headers=vernieuwd)
    assert geweigerd.status_code == 401 and geweigerd.json()["detail"]["code"] == "token_revoked"
    assert (await _toestand(andere_id, "geen-melding"))[2] == andere_toestand
    assert (await client.get("/api/v1/clients/self", headers=andere_headers)).status_code == 200


async def test_kijktelefoon_mag_eigen_wachtwoord_wijzigen_en_hub_niet(admin, koppel):
    client, nieuw = koppel
    assert (await admin.post("/pairing/codes", json={"purpose": "hub", "role": "control"})).status_code == 422
    hub, _ = await nieuw(client)
    telefoon, _ = await nieuw(client, doel="phone")
    pad = "/api/v1/auth/password"
    nieuw_wachtwoord = secrets.token_urlsafe(32)
    body = {"old_password": ADMIN_PW, "new_password": nieuw_wachtwoord, "keep_phones": True}
    assert (await client.post(pad, headers=hub, json=body)).status_code == 403
    assert (await client.post(pad, headers=telefoon, json=body)).status_code == 200
    body.update(old_password=nieuw_wachtwoord, new_password=ADMIN_PW)
    assert (await client.post(pad, headers=telefoon, json=body)).status_code == 200


async def test_teruggezette_koppelingen_vervallen_zonder_schrijvende_get(koppel, tmp_path, monkeypatch):
    client, nieuw = koppel
    oud, oud_id = await nieuw(client)
    vers, vers_id = await nieuw(client)
    async with sessionmaker()() as db:
        koppeling = await db.get(ApiClient, oud_id)
        koppeling.expires_at = None
        oorspronkelijke_datum = (await db.get(ApiClient, vers_id)).expires_at
        await db.commit()

    archief = await backup.run_backup(PW, True, backup_dir=tmp_path)
    async with settings_restored(
        "backup_state", "maintenance", "safe_mode", "restored_from", "restore_snapshot", "restore_report",
        "commissioning_gate",
    ):
        herstel = await backup.restore_file(tmp_path / archief["filename"], PW)
    assert not herstel.migrated
    async with sessionmaker()() as db:
        koppeling = await db.get(ApiClient, oud_id)
        assert koppeling.expires_at == koppeling.created_at + timedelta(days=180)
        assert (await db.get(ApiClient, vers_id)).expires_at == oorspronkelijke_datum

    class Dag181(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=181)

    verbinding = engine().sync_engine
    commits = []
    noteer_commit = commits.append
    event.listen(verbinding, "commit", noteer_commit)
    try:
        pad = "/api/v1/clients/self"
        for headers in (oud, vers):
            antwoord = await client.get(pad, headers=headers)
            assert antwoord.status_code == 200 and antwoord.json()["purpose"] == "hub"
        monkeypatch.setattr(deps, "datetime", Dag181)
        for headers in (oud, vers):
            antwoord = await client.get(pad, headers=headers)
            assert antwoord.status_code == 401 and antwoord.json()["detail"]["code"] == "token_expired"
    finally:
        event.remove(verbinding, "commit", noteer_commit)
    assert commits == []
