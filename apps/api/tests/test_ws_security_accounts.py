"""Security stream: accounts, roles and the audit log (verbeterlijst 18)."""

import json
import re
import uuid

import httpx
import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select, update

from shc_api.db import sessionmaker
from shc_api.deps import require_admin
from shc_api.errors import api_error
from shc_api.models import ApiClient, User
from shc_api.routers.users import ensure_other_admin
from shc_api.store import set_setting

from .conftest import Session

PW = "een-lang-wachtwoord-456"


def _client(app, ip="127.0.0.1"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 40001)), base_url="http://test")


async def _user(admin, username, role="viewer", password=PW):
    r = await admin.post("/users", json={"username": username, "password": password, "role": role})
    assert r.status_code == 200, r.text
    return r.json()


async def _login(c, username, password=PW) -> Session:
    r = await c.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return Session(c, r.json()["csrf_token"])


async def _patch(s: Session, url, body):
    return await s.c.patch("/api/v1" + url, json=body, headers=s.h())


async def _pair(s: Session, app, name, role="control", password=None) -> tuple[str, str]:
    async with sessionmaker()() as db:
        await set_setting(db, "lan_url", "http://192.168.1.20:8088")
        await db.commit()
    body = {"label": name, "role": role}
    if password:
        body["password"] = password
    r = await s.post("/pairing/codes", json=body)
    assert r.status_code == 200, r.text
    code = json.loads(r.json()["qr_payload"])["code"]
    async with _client(app) as phone:
        r = await phone.post("/api/v1/pairing/redeem", json={"code": code, "device_name": name})
        assert r.status_code == 200, r.text
    return r.json()["token"], r.json()["client"]["id"]


# --- every management endpoint needs an admin --------------------------------------------------

ADMIN_ONLY = [
    r"^/api/v1/users",
    r"^/api/v1/auth/users$",
    r"^/api/v1/audit$",
    r"^/api/v1/pairing/codes$",
    r"^/api/v1/clients(/\{client_id\})?$",
    r"^/api/v1/system/backups",
    r"^/api/v1/system/backup-settings$",
    r"^/api/v1/system/restore/(?!jobs/)",  # restore progress is polled without login by design
    r"^/api/v1/system/safe-mode",
    r"^/api/v1/openapi\.json$",
    r"^/api/v1/docs$",
]


def _calls(dependant) -> set:
    out = {dependant.call}
    for d in dependant.dependencies:
        out |= _calls(d)
    return out


def test_every_management_endpoint_requires_admin(app):
    """Phones with role control/viewer and viewer accounts cannot reach users, pairing, backup,
    restore or settings changes: each such route depends on deps.require_admin."""
    checked = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        sensitive = any(re.search(p, route.path) for p in ADMIN_ONLY)
        settings_change = route.path == "/api/v1/system/settings" and route.methods & {"PUT", "PATCH", "POST"}
        if sensitive or settings_change:
            assert require_admin in _calls(route.dependant), f"{route.methods} {route.path} is not admin-only"
            checked.append(route.path)
    assert "/api/v1/users/{user_id}" in checked and "/api/v1/audit" in checked
    assert any(p.startswith("/api/v1/system/backups") for p in checked)


async def test_control_phone_and_viewer_are_kept_out(app, admin, home):
    token, _cid = await _pair(admin, app, "sec-control-phone")
    h = {"Authorization": f"Bearer {token}"}
    await _user(admin, "sec_acc_viewer")
    async with _client(app) as c:
        for url in ("/api/v1/users", "/api/v1/audit", "/api/v1/clients", "/api/v1/system/backups"):
            assert (await c.get(url, headers=h)).status_code == 403, url
        assert (await c.post("/api/v1/pairing/codes", json={}, headers=h)).status_code == 403
        assert (await c.put("/api/v1/system/settings", json={}, headers=h)).status_code == 403
        v = await _login(c, "sec_acc_viewer")
        for url in ("/users", "/audit", "/clients", "/system/backups"):
            assert (await v.get(url)).status_code == 403, url
        assert (await v.post("/users", json={"username": "x_y", "password": PW, "role": "admin"})).status_code == 403
        # A viewer cannot resume a zone (Terug naar schema), and that is not audited as a request.
        zid = (await v.get(f"/homes/{home['id']}/zones")).json()[0]["id"]
        r = await v.post(f"/zones/{zid}/resume")
        assert r.status_code == 403 and r.json()["detail"]["message"] == "Alleen-lezen toegang."
    rows = (await admin.get("/audit?action=request&actor=sec_acc_viewer")).json()
    assert rows == []


# --- users -----------------------------------------------------------------------------------


async def test_user_list_create_and_validation(admin):
    u = await _user(admin, "sec_list")
    assert u["role"] == "viewer" and u["disabled"] is False and u["locked"] is False
    assert u["active_sessions"] == 0 and u["active_phones"] == 0 and u["password_changed_at"]
    users = (await admin.get("/users")).json()
    beheer = next(x for x in users if x["username"] == "beheer")
    assert beheer["role"] == "admin" and beheer["active_sessions"] >= 1
    assert "password_hash" not in beheer
    r = await admin.post("/users", json={"username": "SEC_LIST", "password": PW, "role": "viewer"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "username_taken"
    r = await admin.post("/users", json={"username": "sec_kort", "password": "kort", "role": "viewer"})
    assert r.status_code == 422
    r = await admin.post("/users", json={"username": "sec_rol", "password": PW, "role": "superuser"})
    assert r.status_code == 422
    # Old path keeps working for existing clients.
    r = await admin.post("/auth/users", json={"username": "sec_legacy", "password": PW, "role": "viewer"})
    assert r.status_code == 200 and r.json()["username"] == "sec_legacy"


async def test_cannot_change_or_delete_yourself(admin):
    me = (await admin.get("/auth/me")).json()
    assert me["is_admin"] is True and me["can_control"] is True and me["via"] == "cookie"
    r = await _patch(admin, f"/users/{me['id']}", {"role": "viewer"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "self_change"
    r = await _patch(admin, f"/users/{me['id']}", {"disabled": True})
    assert r.status_code == 409
    r = await admin.delete(f"/users/{me['id']}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "self_delete"
    r = await admin.post(f"/users/{me['id']}/password", json={"password": PW})
    assert r.status_code == 409


async def test_last_admin_is_protected():
    """With every other admin gone (inside a transaction that is rolled back) the target is the last."""
    async with sessionmaker()() as db:
        target = (await db.execute(select(User).where(User.username == "beheer"))).scalar_one()
        db.add(User(username="sec_tmp_admin", password_hash="x", role="admin"))
        await db.flush()
        await ensure_other_admin(db, target)  # another enabled admin exists: fine
        await db.execute(update(User).where(User.role == "admin", User.id != target.id).values(disabled=True))
        with pytest.raises(type(api_error(409, "x", "y"))) as exc:
            await ensure_other_admin(db, target)
        assert exc.value.status_code == 409 and exc.value.detail["code"] == "last_admin"
        await db.rollback()


async def test_role_change_and_disable_revokes_access(app, admin):
    u = await _user(admin, "sec_promote", role="admin")
    async with _client(app) as c:
        s = await _login(c, "sec_promote")
        token, _ = await _pair(s, app, "sec-promote-phone")
        h = {"Authorization": f"Bearer {token}"}
        r = await _patch(admin, f"/users/{u['id']}", {"role": "viewer"})
        assert r.status_code == 200 and r.json()["role"] == "viewer"
        # Demoted: the web session is read-only now, the control phone too.
        assert (await s.get("/users")).status_code == 403
        assert (await c.get("/api/v1/auth/me", headers=h)).json()["can_control"] is False
        r = await _patch(admin, f"/users/{u['id']}", {"disabled": True})
        assert r.status_code == 200 and r.json()["disabled"] is True and r.json()["active_sessions"] == 0
        assert (await s.get("/auth/me")).status_code == 401
        r = await c.get("/api/v1/auth/me", headers=h)
        assert r.status_code == 401
        assert (await c.post("/api/v1/auth/login", json={"username": "sec_promote", "password": PW})).status_code == 401
        r = await _patch(admin, f"/users/{u['id']}", {"disabled": False, "role": "admin"})
        assert r.status_code == 200
        # Phones stay revoked after re-enabling; the user logs in again and pairs again.
        assert (await c.get("/api/v1/auth/me", headers=h)).json()["detail"]["code"] == "token_revoked"
        await _login(c, "sec_promote")


async def test_delete_user_removes_sessions_and_phones(app, admin):
    u = await _user(admin, "sec_delete", role="admin")
    async with _client(app) as c:
        s = await _login(c, "sec_delete")
        token, cid = await _pair(s, app, "sec-delete-phone")
        assert (await admin.delete(f"/users/{u['id']}")).status_code == 204
        assert (await s.get("/auth/me")).status_code == 401
        assert (await c.get("/api/v1/homes", headers={"Authorization": f"Bearer {token}"})).status_code == 401
    assert all(x["username"] != "sec_delete" for x in (await admin.get("/users")).json())
    async with sessionmaker()() as db:
        assert await db.get(ApiClient, uuid.UUID(cid)) is None
    assert (await admin.delete(f"/users/{u['id']}")).status_code == 404


# --- passwords -------------------------------------------------------------------------------


async def test_change_own_password_revokes_other_sessions_and_phones(app, admin):
    await _user(admin, "sec_pw", role="admin")
    async with _client(app) as a, _client(app) as b, _client(app) as phone:
        sa = await _login(a, "sec_pw")
        sb = await _login(b, "sec_pw")
        token, _ = await _pair(sa, app, "sec-pw-phone")
        h = {"Authorization": f"Bearer {token}"}
        r = await sa.post("/auth/password", json={"old_password": "fout", "new_password": "nieuw-wachtwoord-789"})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "wrong_password"
        async with sessionmaker()() as db:
            u = (await db.execute(select(User).where(User.username == "sec_pw"))).scalar_one()
        assert u.failed_logins == 1
        r = await sa.post("/auth/password", json={"old_password": PW, "new_password": PW})
        assert r.status_code == 422
        r = await sa.post("/auth/password", json={"old_password": PW, "new_password": "kort"})
        assert r.status_code == 422
        new = "nieuw-wachtwoord-789"
        r = await sa.post("/auth/password", json={"old_password": PW, "new_password": new})
        assert r.status_code == 200, r.text
        assert r.json() == {"sessions_ended": 1, "phones_revoked": 1}
        assert (await sa.get("/auth/me")).status_code == 200  # the session making the change stays
        assert (await sb.get("/auth/me")).status_code == 401
        r = await phone.get("/api/v1/homes", headers=h)
        assert r.status_code == 401 and r.json()["detail"]["code"] == "token_revoked"
        async with sessionmaker()() as db:
            u = (await db.execute(select(User).where(User.username == "sec_pw"))).scalar_one()
        assert u.failed_logins == 0 and u.password_changed_at is not None
        assert (await a.post("/api/v1/auth/login", json={"username": "sec_pw", "password": PW})).status_code == 401
        sb = await _login(b, "sec_pw", new)
        # keep_phones: the phone keeps working, other web sessions still end.
        token2, _ = await _pair(sa, app, "sec-pw-phone2")
        h2 = {"Authorization": f"Bearer {token2}"}
        r = await sa.post("/auth/password", json={"old_password": new, "new_password": PW, "keep_phones": True})
        assert r.status_code == 200 and r.json() == {"sessions_ended": 1, "phones_revoked": 0}
        assert (await phone.get("/api/v1/homes", headers=h2)).status_code == 200
        assert (await sb.get("/auth/me")).status_code == 401
        # From the phone itself: its own token survives, the web session ends.
        r = await phone.post("/api/v1/auth/password", json={"old_password": PW, "new_password": new}, headers=h2)
        assert r.status_code == 200 and r.json()["sessions_ended"] == 1
        assert (await phone.get("/api/v1/homes", headers=h2)).status_code == 200
        assert (await sa.get("/auth/me")).status_code == 401


async def test_admin_password_reset_revokes_everything(app, admin):
    u = await _user(admin, "sec_reset", role="admin")
    async with _client(app) as c:
        s = await _login(c, "sec_reset")
        token, _ = await _pair(s, app, "sec-reset-phone")
        r = await admin.post(f"/users/{u['id']}/password", json={"password": "door-beheer-gezet-1"})
        assert r.status_code == 200 and r.json() == {"sessions_ended": 1, "phones_revoked": 1}
        assert (await s.get("/auth/me")).status_code == 401
        assert (await c.get("/api/v1/homes", headers={"Authorization": f"Bearer {token}"})).status_code == 401
        await _login(c, "sec_reset", "door-beheer-gezet-1")
        r = await admin.post(f"/users/{u['id']}/password", json={"password": "kort"})
        assert r.status_code == 422


# --- audit log ---------------------------------------------------------------------------------


async def test_audit_middleware_and_endpoint(admin):
    u = await _user(admin, "sec_audit")
    r = await _patch(admin, f"/users/{u['id']}", {"unlock": True})
    assert r.status_code == 200
    rows = (await admin.get("/audit?action=request&limit=20")).json()
    row = next(x for x in rows if x["target"] == "PATCH /api/v1/users/{user_id}")
    assert row["actor"] == "beheer" and row["detail"]["status"] == 200
    assert row["detail"]["params"] == {"user_id": u["id"]} and row["detail"]["via"] == "cookie"
    assert PW not in json.dumps(rows)  # request bodies are never logged
    # The POST /users that created the user is there too, next to the domain event.
    assert any(x["target"] == "POST /api/v1/users" for x in rows)
    created = (await admin.get("/audit?action=user_created")).json()
    assert any(x["target"] == "sec_audit" for x in created)
    # Failed requests are not logged as requests.
    r = await _patch(admin, f"/users/{u['id']}", {"role": "superuser"})
    assert r.status_code == 422
    rows2 = (await admin.get("/audit?action=request&limit=5")).json()
    assert rows2[0]["id"] >= row["id"] and all(x["detail"]["status"] < 400 for x in rows2)
    # Paging: newest first, "before" continues below an id.
    page1 = (await admin.get("/audit?limit=3")).json()
    page2 = (await admin.get(f"/audit?limit=3&before={page1[-1]['id']}")).json()
    assert len(page1) == 3 and page2 and page2[0]["id"] < page1[-1]["id"]
    assert [x["id"] for x in page1] == sorted((x["id"] for x in page1), reverse=True)
    assert (await admin.get("/audit?limit=1000")).status_code == 422
