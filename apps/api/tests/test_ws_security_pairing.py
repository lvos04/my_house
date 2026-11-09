"""Security stream: phone pairing, roles, token lifetime and server identity (verbeterlijst 17, 18)."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import ApiClient, PairingCode, Setting
from shc_api.routers import pairing
from shc_api.store import get_setting, set_setting

LAN = "http://192.168.1.20:8088"


def _client(app, ip="127.0.0.1"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 40002)), base_url="http://test")


async def _addresses(lan, remote=None):
    async with sessionmaker()() as db:
        await set_setting(db, "lan_url", lan)
        await set_setting(db, "remote_url", remote)
        await db.commit()


@pytest.fixture
async def lan():
    await _addresses(LAN)
    yield LAN
    await _addresses(LAN)


async def _code(admin, **body):
    r = await admin.post("/pairing/codes", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _redeem(app, code, name, ip="127.0.0.1"):
    async with _client(app, ip) as phone:
        return await phone.post("/api/v1/pairing/redeem", json={"code": code, "device_name": name})


async def test_pairing_requires_a_configured_home_address(admin, home, app, lan, monkeypatch):
    monkeypatch.setattr(get_settings(), "lan_url", None)
    await _addresses(None)
    r = await admin.post("/pairing/codes", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "lan_url_required"
    assert "Systeem" in r.json()["detail"]["message"]
    # The environment (SHC_LAN_URL) counts as configured.
    monkeypatch.setattr(get_settings(), "lan_url", "http://nas.local:8088")
    payload = json.loads((await _code(admin))["qr_payload"])
    assert payload["lan"] == "http://nas.local:8088"
    monkeypatch.setattr(get_settings(), "lan_url", None)
    # A public name is no home address (the token would travel unencrypted over the internet).
    await _addresses("http://huis.voorbeeld.nl:8088")
    r = await admin.post("/pairing/codes", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "lan_url_invalid"
    await _addresses("http://127.0.0.1:8088")
    assert (await admin.post("/pairing/codes", json={})).status_code == 409
    # Remote must be https.
    await _addresses(LAN, "http://huis.voorbeeld.nl")
    r = await admin.post("/pairing/codes", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "remote_url_invalid"
    await _addresses(LAN, "https://huis.voorbeeld.nl")
    payload = json.loads((await _code(admin))["qr_payload"])
    assert payload["lan"] == LAN and payload["remote"] == "https://huis.voorbeeld.nl"


async def test_server_identity_in_qr_redeem_and_public_endpoint(admin, home, app, lan):
    async with _client(app) as anon:
        r = await anon.get("/api/v1/pairing/identity")
        assert r.status_code == 200
        ident = r.json()
    uuid.UUID(ident["server_id"])
    assert ident["name"] == home["name"]
    created = await _code(admin, label="sec-ident")
    payload = json.loads(created["qr_payload"])
    assert payload["server_id"] == ident["server_id"] == created["server_id"]
    assert payload["role"] == "control" and payload["v"] == 1 and payload["type"] == "shc-pair"
    r = await _redeem(app, created["code"], "sec-ident")
    assert r.status_code == 200
    body = r.json()
    assert body["server"]["server_id"] == ident["server_id"]
    assert body["server"]["lan_url"] == LAN and body["server"]["home_name"] == home["name"]
    assert body["client"]["role"] == "control"
    expires = datetime.fromisoformat(body["client"]["expires_at"].replace("Z", "+00:00"))
    assert timedelta(days=179) < expires - datetime.now(UTC) <= timedelta(days=180)
    # Stable across calls.
    async with _client(app) as anon:
        assert (await anon.get("/api/v1/pairing/identity")).json()["server_id"] == ident["server_id"]


async def test_pairing_roles(admin, home, app, lan):
    zid = (await admin.get(f"/homes/{home['id']}/zones")).json()[0]["id"]
    # viewer: read only
    v = await _code(admin, label="sec-viewer", role="viewer")
    r = await _redeem(app, v["code"], "sec-viewer")
    assert r.json()["client"]["role"] == "viewer"
    hv = {"Authorization": f"Bearer {r.json()['token']}"}
    # control (default): operate, not manage
    c = await _code(admin, label="sec-control")
    r = await _redeem(app, c["code"], "sec-control")
    hc = {"Authorization": f"Bearer {r.json()['token']}"}
    # admin: needs the creator's password
    r = await admin.post("/pairing/codes", json={"role": "admin"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "password_required"
    r = await admin.post("/pairing/codes", json={"role": "admin", "password": "niet-mijn-wachtwoord"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "wrong_password"
    from .conftest import ADMIN_PW

    a = await _code(admin, label="sec-admin", role="admin", password=ADMIN_PW)
    r = await _redeem(app, a["code"], "sec-admin")
    assert r.json()["client"]["role"] == "admin"
    ha = {"Authorization": f"Bearer {r.json()['token']}"}
    async with _client(app) as phone:
        assert (await phone.get(f"/api/v1/zones/{zid}", headers=hv)).status_code == 200
        assert (await phone.post(f"/api/v1/zones/{zid}/resume", headers=hv)).status_code == 403
        assert (await phone.post(f"/api/v1/zones/{zid}/resume", headers=hc)).status_code == 200
        assert (await phone.get("/api/v1/users", headers=hc)).status_code == 403
        # Even an admin phone cannot manage users, pairing or backups (web session only).
        r = await phone.get("/api/v1/users", headers=ha)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "web_session_required"
        assert (await phone.get("/api/v1/homes", headers=ha)).status_code == 200
        me = (await phone.get("/api/v1/auth/me", headers=hv)).json()
        assert me["via"] == "token" and me["client_role"] == "viewer" and me["can_control"] is False
    clients = (await admin.get("/clients")).json()
    by_name = {x["name"]: x for x in clients}
    assert by_name["sec-viewer"]["role"] == "viewer" and by_name["sec-admin"]["role"] == "admin"
    assert by_name["sec-control"]["user"] == "beheer" and by_name["sec-control"]["expires_at"]
    # The role lives on the code itself (migration 0003); no settings rows any more.
    async with sessionmaker()() as db:
        left = set((await db.execute(select(Setting.key).where(Setting.key.like("pairing\\_role:%")))).scalars())
        roles = dict(
            (await db.execute(select(PairingCode.label, PairingCode.role).where(PairingCode.label.like("sec-%")))).all()
        )
    assert not left
    assert {k: roles[k] for k in ("sec-viewer", "sec-control", "sec-admin")} == {
        "sec-viewer": "viewer",
        "sec-control": "control",
        "sec-admin": "admin",
    }
    assert (await admin.post("/pairing/codes", json={"role": "superuser"})).status_code == 422


async def test_token_expiry_refresh_and_unpair(admin, home, app, lan):
    c = await _code(admin, label="sec-life")
    r = await _redeem(app, c["code"], "sec-life")
    token, cid = r.json()["token"], uuid.UUID(r.json()["client"]["id"])
    h = {"Authorization": f"Bearer {token}"}
    async with _client(app) as phone:
        me = (await phone.get("/api/v1/clients/self", headers=h)).json()
        assert me["id"] == str(cid) and me["role"] == "control" and me["user"] == "beheer"
        # Cookie sessions have no "self" client.
        assert (await admin.get("/clients/self")).status_code == 403
        r = await phone.post("/api/v1/clients/self/refresh", headers=h)
        assert r.status_code == 200
        new_token = r.json()["token"]
        assert new_token != token and new_token.startswith("shc_")
        h2 = {"Authorization": f"Bearer {new_token}"}
        assert (await phone.get("/api/v1/homes", headers=h2)).status_code == 200
        r = await phone.get("/api/v1/homes", headers=h)  # the old token stopped working
        assert r.status_code == 401 and r.json()["detail"]["code"] == "auth_required"
        assert (await phone.get("/api/v1/clients/self", headers=h2)).json()["refreshed_at"]
        # Expired tokens are refused with a code the app understands.
        async with sessionmaker()() as db:
            await db.execute(
                update(ApiClient).where(ApiClient.id == cid).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
            )
            await db.commit()
        r = await phone.get("/api/v1/homes", headers=h2)
        assert r.status_code == 401 and r.json()["detail"]["code"] == "token_expired"
        assert (await phone.post("/api/v1/clients/self/refresh", headers=h2)).status_code == 401
        assert next(x for x in (await admin.get("/clients")).json() if x["id"] == str(cid))["expired"] is True
        # Tokens from before expiry existed get an expiry date on first use.
        async with sessionmaker()() as db:
            await db.execute(update(ApiClient).where(ApiClient.id == cid).values(expires_at=None, last_used_at=None))
            await db.commit()
        assert (await phone.get("/api/v1/homes", headers=h2)).status_code == 200
        async with sessionmaker()() as db:
            row = await db.get(ApiClient, cid)
        assert row.expires_at and row.expires_at > datetime.now(UTC) + timedelta(days=179)
        # "Ontkoppelen" in the app revokes the token on the server.
        assert (await phone.delete("/api/v1/clients/self", headers=h2)).status_code == 204
        r = await phone.get("/api/v1/homes", headers=h2)
        assert r.status_code == 401 and r.json()["detail"]["code"] == "token_revoked"
    assert next(x for x in (await admin.get("/clients")).json() if x["id"] == str(cid))["revoked"] is True


async def test_redeem_is_rate_limited_per_address(admin, home, app, lan):
    pairing._redeem_attempts.clear()
    good = await _code(admin, label="sec-rl")
    ip = "198.51.100.77"
    for _ in range(10):
        r = await _redeem(app, "ZZZZ-ZZZZ", "raden", ip)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_code"
    r = await _redeem(app, good["code"], "sec-rl", ip)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited" and "retry-after" in r.headers
    # The code itself is still unused: another address can redeem it.
    r = await _redeem(app, good["code"], "sec-rl", "198.51.100.78")
    assert r.status_code == 200
    pairing._redeem_attempts.clear()


async def test_revoked_phone_gets_specific_code(admin, home, app, lan):
    c = await _code(admin, label="sec-revoke")
    r = await _redeem(app, c["code"], "sec-revoke")
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert (await admin.delete(f"/clients/{r.json()['client']['id']}")).status_code == 204
    async with _client(app) as phone:
        r = await phone.get("/api/v1/homes", headers=h)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "token_revoked"
    assert "niet meer gekoppeld" in r.json()["detail"]["message"]
    async with sessionmaker()() as db:
        assert await get_setting(db, "server_id")
