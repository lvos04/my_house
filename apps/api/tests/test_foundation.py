"""Permission model and settings introduced with the restructure (phone roles, Control, cookie flag)."""

import json
import uuid

import httpx
import pytest
from sqlalchemy import update

from shc_api.config import Settings
from shc_api.db import sessionmaker
from shc_api.models import ApiClient

from .conftest import Session


async def _pair_phone(admin, app, name: str) -> tuple[str, uuid.UUID]:
    from shc_api.store import set_setting

    async with sessionmaker()() as db:  # pairing needs a configured home address (verbeterlijst 17)
        await set_setting(db, "lan_url", "http://192.168.1.20:8088")
        await db.commit()
    code = json.loads((await admin.post("/pairing/codes", json={"label": name})).json()["qr_payload"])["code"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as phone:
        r = await phone.post("/api/v1/pairing/redeem", json={"code": code, "device_name": name})
        assert r.status_code == 200, r.text
    cid = next(c["id"] for c in (await admin.get("/clients")).json() if c["name"] == name)
    return r.json()["token"], uuid.UUID(cid)


async def _set_role(client_id: uuid.UUID, role: str) -> None:
    async with sessionmaker()() as db:
        await db.execute(update(ApiClient).where(ApiClient.id == client_id).values(role=role))
        await db.commit()


async def test_phone_roles_control_vs_admin(admin, home, app):
    token, cid = await _pair_phone(admin, app, "Rolfoon")
    zid = (await admin.get(f"/homes/{home['id']}/zones")).json()[0]["id"]
    h = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as phone:
        # New pairings default to "control": may operate devices, not manage the system.
        assert (await phone.post(f"/api/v1/zones/{zid}/resume", headers=h)).status_code == 200
        r = await phone.post("/api/v1/pairing/codes", json={}, headers=h)
        assert r.status_code == 403
        await _set_role(cid, "viewer")
        r = await phone.post(f"/api/v1/zones/{zid}/resume", headers=h)
        assert r.status_code == 403 and r.json()["detail"]["message"] == "Alleen-lezen toegang."
        assert (await phone.get(f"/api/v1/zones/{zid}", headers=h)).status_code == 200
        await _set_role(cid, "admin")  # phones paired before migration 0002
        assert (await phone.post("/api/v1/pairing/codes", json={}, headers=h)).status_code == 200


async def test_viewer_cannot_resume_or_control(admin, home, app):
    await admin.post("/auth/users", json={"username": "kijker2", "password": "kijker-wachtwoord-456", "role": "viewer"})
    zid = (await admin.get(f"/homes/{home['id']}/zones")).json()[0]["id"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/auth/login", json={"username": "kijker2", "password": "kijker-wachtwoord-456"})
        v = Session(c, r.json()["csrf_token"])
        assert (await v.post(f"/zones/{zid}/resume")).status_code == 403
        assert (await v.post(f"/zones/{zid}/command", json={"target_c": 21})).status_code == 403
        assert (await v.post(f"/lights/groups/{uuid.uuid4()}", json={"on": True})).status_code == 403


@pytest.mark.parametrize(
    ("value", "http", "https"),
    [("auto", False, True), ("true", True, True), ("false", False, False), (True, True, True), ("0", False, False)],
)
def test_cookie_secure_setting(value, http, https):
    s = Settings(cookie_secure=value)
    assert (s.cookie_secure_for("http"), s.cookie_secure_for("https")) == (http, https)
