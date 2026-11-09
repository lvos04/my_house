"""Verbeterlijst 17 and 18 follow-ups: server identity challenge, admin phones kept out of user/pairing/
backup management, legacy phone tokens (migration 0004) and the remaining audit-log gaps."""

import asyncio
import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from shc_api.db import sessionmaker
from shc_api.models import AuditLog, ClimateZone
from shc_api.store import raise_alert, set_setting

from .conftest import ADMIN_PW
from .ws_devices_support import add_device, new_home

LAN = "http://192.168.1.20:8088"


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def lan():
    async with sessionmaker()() as db:
        await set_setting(db, "lan_url", LAN)
        await db.commit()
    return LAN


async def _pair(admin, app, name, role="control"):
    body = {"label": name, "role": role}
    if role == "admin":
        body["password"] = ADMIN_PW
    r = await admin.post("/pairing/codes", json=body)
    assert r.status_code == 200, r.text
    async with _client(app) as phone:
        r = await phone.post("/api/v1/pairing/redeem", json={"code": r.json()["code"], "device_name": name})
    assert r.status_code == 200, r.text
    return r.json()


# --- 17: identity with a challenge ------------------------------------------------------------------


def _expected_proof(token: str, server_id: str, nonce: str) -> str:
    """What the phone computes: it only knows its token."""
    key = hashlib.sha256(token.encode()).hexdigest().encode()
    return hmac.new(key, f"shc-identity-v1\n{server_id}\n{nonce}".encode(), hashlib.sha256).hexdigest()


async def test_identity_answers_the_phones_challenge(admin, home, app, lan):
    paired = await _pair(admin, app, "gap-identity")
    token, cid = paired["token"], paired["client"]["id"]
    nonce = "n0nce-" + uuid.uuid4().hex
    async with _client(app) as anon:
        plain = (await anon.get("/api/v1/pairing/identity")).json()
        assert "proof" not in plain and plain["server_id"]
        r = await anon.get("/api/v1/pairing/identity", params={"nonce": nonce, "client_id": cid})
        assert r.status_code == 200
        body = r.json()
        assert body["proof_alg"] == "hmac-sha256-v1"
        assert body["proof"] == _expected_proof(token, body["server_id"], nonce)
        # Another nonce gives another proof (no replay of an earlier answer).
        other = (await anon.get("/api/v1/pairing/identity", params={"nonce": nonce + "x", "client_id": cid})).json()
        assert other["proof"] != body["proof"]
        # A device that only knows the public server id cannot answer: unknown client -> no proof.
        r = await anon.get("/api/v1/pairing/identity", params={"nonce": nonce, "client_id": str(uuid.uuid4())})
        assert r.json()["proof"] is None
        # Nonce too short or with odd characters is refused.
        assert (
            await anon.get("/api/v1/pairing/identity", params={"nonce": "kort", "client_id": cid})
        ).status_code == 422
        bad = {"nonce": "x" * 20 + "/../", "client_id": cid}
        assert (await anon.get("/api/v1/pairing/identity", params=bad)).status_code == 422
    # After a token refresh the proof follows the new token.
    async with _client(app) as phone:
        r = await phone.post("/api/v1/clients/self/refresh", headers={"Authorization": f"Bearer {token}"})
        new = r.json()["token"]
        body = (await phone.get("/api/v1/pairing/identity", params={"nonce": nonce, "client_id": cid})).json()
    assert body["proof"] == _expected_proof(new, body["server_id"], nonce)
    assert body["proof"] != _expected_proof(token, body["server_id"], nonce)


# --- 18: an admin phone cannot manage users, pairing or backups ------------------------------------


async def test_admin_phone_is_refused_for_users_pairing_and_backups(admin, home, app, lan):
    paired = await _pair(admin, app, "gap-admin-phone", role="admin")
    h = {"Authorization": f"Bearer {paired['token']}"}
    uid = (await admin.get("/users")).json()[0]["id"]
    refused = [
        ("GET", "/users", None),
        ("POST", "/users", {"username": "dief", "password": "een-lang-wachtwoord-1", "role": "admin"}),
        ("POST", "/auth/users", {"username": "dief2", "password": "een-lang-wachtwoord-1", "role": "admin"}),
        ("PATCH", f"/users/{uid}", {"disabled": True}),
        ("POST", f"/users/{uid}/password", {"password": "een-lang-wachtwoord-2"}),
        ("DELETE", f"/users/{uid}", None),
        ("POST", "/pairing/codes", {"role": "control"}),
        ("GET", "/clients", None),
        ("DELETE", f"/clients/{uuid.uuid4()}", None),
        ("PUT", "/system/settings", {"lan_url": LAN}),
        ("GET", "/system/backups", None),
        ("POST", "/system/backups", {"passphrase": "lange wachtwoordzin 1"}),
        ("GET", f"/system/backups/{uuid.uuid4()}/download", None),
        ("GET", "/system/backup-settings", None),
        ("PUT", "/system/backup-settings", {"hour": 4}),
        ("POST", "/system/restore/verify", None),
        ("DELETE", f"/system/restore/uploads/{uuid.uuid4().hex}", None),
        ("POST", "/system/restore/apply", {"upload_id": uuid.uuid4().hex, "passphrase": "lange wachtwoordzin 1"}),
        ("GET", "/system/restore/compare", None),
        ("GET", "/system/notify", None),
        ("PUT", "/system/notify", {"url": "https://ntfy.sh/geheim"}),
        ("POST", "/system/notify/test", None),
    ]
    async with _client(app) as phone:
        for method, url, body in refused:
            r = await phone.request(method, "/api/v1" + url, json=body, headers=h)
            assert r.status_code == 403, (method, url, r.status_code, r.text)
            assert r.json()["detail"]["code"] == "web_session_required", (method, url)
        # Still a full admin for the rest (devices, homes, the audit log).
        assert (await phone.get("/api/v1/homes", headers=h)).status_code == 200
        assert (await phone.get("/api/v1/audit", headers=h)).status_code == 200
        assert (await phone.get("/api/v1/auth/me", headers=h)).json()["is_admin"] is True
    # The web session of the same admin may do all of it.
    assert (await admin.get("/users")).status_code == 200
    assert (await admin.get("/system/backups")).status_code == 200
    assert (await admin.get("/clients")).status_code == 200


# --- 18: legacy phone tokens (migration 0004, scratch database) ---------------------------------------


# The database of a NAS need not run in UTC (infra/compose/compose.yaml gives it TZ=Europe/Amsterdam) and
# the migration must give the same moments in every session time zone. In the two zones with daylight
# saving time one token's 180 days always span a change of UTC offset, whatever day the test runs.
@pytest.mark.parametrize("tz", ["UTC", "Europe/Amsterdam", "Australia/Sydney"])
async def test_migration_0004_limits_legacy_phone_tokens(tz):
    from zoneinfo import ZoneInfo

    from alembic import command
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from shc_api import backup as bk

    from .test_ws_backup_format import temp_db

    async def sql(url, stmt, params=None, fetch=True):
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.begin() as c:
                res = await c.execute(text(stmt), params or {})
                return res.all() if fetch else None
        finally:
            await engine.dispose()

    uid = uuid.uuid4()
    unused, recent, older, viewer, revoked, current = (uuid.uuid4() for _ in range(6))
    now = datetime.now(UTC)
    zone = ZoneInfo(tz)

    def offset_changes(t):
        return t.astimezone(zone).utcoffset() != (t + timedelta(days=180)).astimezone(zone).utcoffset()

    # A last use 1-149 days ago (so 180 days later is still past the 30-day minimum) whose 180 days end at a
    # different UTC offset than they start (a span over two changes would cancel out, hence the search).
    # Both zones have such a day in that range on every day of the year.
    older_use = now - timedelta(days=149)
    if tz != "UTC":
        older_use = next((t for t in (now - timedelta(days=149 - d) for d in range(149)) if offset_changes(t)), None)
        assert older_use is not None
    async with temp_db("0003") as url:
        # Every new session in this scratch database (the migration's too) gets this time zone.
        await sql(url, f"ALTER DATABASE \"{url.rsplit('/', 1)[1]}\" SET timezone TO '{tz}'", fetch=False)
        assert (await sql(url, "SHOW timezone"))[0][0] == tz
        await sql(
            url,
            "INSERT INTO users (id, username, password_hash, role, disabled, created_at) "
            "VALUES (:id, 'm', 'x', 'admin', false, now())",
            {"id": uid},
            fetch=False,
        )
        rows = [
            # paired before 0002 (role from the server default), never used since
            (unused, None, now - timedelta(days=400), None, None, None),
            # paired before 0002, used yesterday but the clock never started (expires_at NULL)
            (recent, None, now - timedelta(days=300), now - timedelta(days=1), None, None),
            # the same, last used across a change of UTC offset in `tz` (see older_use)
            (older, None, now - timedelta(days=320), older_use, None, None),
            (viewer, "viewer", now - timedelta(days=10), None, None, None),
            (revoked, None, now - timedelta(days=50), None, now - timedelta(days=5), None),
            (current, "admin", now - timedelta(days=3), now, None, now + timedelta(days=177)),
        ]
        for cid, role, created, used, rev, exp in rows:
            await sql(
                url,
                "INSERT INTO api_clients (id, user_id, name, token_hash, created_at, last_used_at, revoked_at, "
                "expires_at, role) VALUES (:id, :u, :n, :h, :c, :l, :r, :e, coalesce(CAST(:role AS text), 'admin'))",
                {
                    "id": cid,
                    "u": uid,
                    "n": f"tel-{cid.hex[:4]}",
                    "h": uuid.uuid4().hex,
                    "c": created,
                    "l": used,
                    "r": rev,
                    "e": exp,
                    "role": role,
                },
                fetch=False,
            )

        await asyncio.to_thread(command.upgrade, bk.alembic_config(url), "0004")
        got = {r[0]: (r[1], r[2]) for r in await sql(url, "SELECT id, role, expires_at FROM api_clients")}
        assert got[unused][0] == "control" and got[recent][0] == "control" and got[older][0] == "control"
        assert got[viewer][0] == "viewer" and got[current] == ("admin", rows[5][5])
        # 180 days after the last use, but never sooner than 30 days from now (time to refresh). Both count
        # as elapsed time, exactly like CLIENT_TTL in deps.py (180 x 24 h), whatever the session time zone.
        assert abs(got[unused][1] - (now + timedelta(days=30))) < timedelta(minutes=5)
        assert got[recent][1] == rows[1][3] + timedelta(days=180)
        assert got[older][1] == rows[2][3] + timedelta(days=180)
        assert got[revoked][1] == rows[4][4]
        assert not [k for k, v in got.items() if v[1] is None]
        flagged = await sql(
            url,
            "SELECT detail->>'client', detail->>'old_role', detail->>'expires_at' FROM audit_log "
            "WHERE action = 'legacy_phone_limited'",
        )
        assert {r[0] for r in flagged} == {str(unused), str(recent), str(older), str(viewer)}
        # The audit line names the same moment as the row.
        assert all(datetime.fromisoformat(r[2]) == got[uuid.UUID(r[0])][1] for r in flagged)
        default = await sql(
            url,
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'api_clients' AND column_name = 'role'",
        )
        assert "control" in default[0][0]

        await asyncio.to_thread(command.downgrade, bk.alembic_config(url), "0003")
        back = dict(await sql(url, "SELECT id, role FROM api_clients"))
        assert back[unused] == "admin" and back[recent] == "admin" and back[older] == "admin"
        assert back[viewer] == "viewer"
        default = await sql(
            url,
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name = 'api_clients' AND column_name = 'role'",
        )
        assert "admin" in default[0][0]
        await asyncio.to_thread(command.upgrade, bk.alembic_config(url), "head")
        assert (await sql(url, "SELECT version_num FROM alembic_version"))[0][0] == bk.head_revision()


# --- 18: every change in the audit log ---------------------------------------------------------------


async def _audit_rows(action: str, since: datetime) -> list[AuditLog]:
    async with sessionmaker()() as db:
        return list(
            (await db.execute(select(AuditLog).where(AuditLog.action == action, AuditLog.at >= since))).scalars()
        )


async def test_remaining_changes_are_audited(admin):
    since = datetime.now(UTC) - timedelta(seconds=1)
    home_id = await new_home(admin)
    # alert acknowledged
    async with sessionmaker()() as db:
        a = await raise_alert(db, home_id, f"gap-audit:{uuid.uuid4()}", "warn", "Testmelding", "x", "system")
        await db.commit()
    assert (await admin.post(f"/alerts/{a.id}/ack")).status_code == 200
    rows = await _audit_rows("alert_acknowledged", since)
    assert rows and rows[-1].target == "Testmelding" and rows[-1].detail["alert"] == str(a.id)
    # adaptive switched off
    async with sessionmaker()() as db:
        zid = (await db.execute(select(ClimateZone.id).where(ClimateZone.home_id == home_id))).scalars().first()
    assert (await admin.post(f"/zones/{zid}/adaptive/disable")).status_code == 200
    assert [r for r in await _audit_rows("adaptive_disable", since) if r.target == str(zid)]
    # light group command and scene
    group = await add_device(home_id, "light_group", "Woonkamer", provider="hue")
    scene = await add_device(home_id, "scene", "Avond", provider="hue")
    r = await admin.post(f"/lights/groups/{group.id}", json={"on": True, "brightness": 40})
    assert r.status_code == 202, r.text
    r = await admin.post(f"/lights/groups/{group.id}", json={"scene_id": str(scene.id)})
    assert r.status_code == 202, r.text
    rows = [r for r in await _audit_rows("light_command", since) if r.detail.get("device") == str(group.id)]
    assert {r.detail["command"] for r in rows} == {"set_group", "recall_scene"}
    assert any(r.detail.get("brightness") == 40 and r.detail.get("on") is True for r in rows)
    # Zigbee permit join
    await add_device(home_id, "zigbee_bridge", "Zigbee", provider="zigbee")
    r = await admin.post("/integrations/zigbee/permit-join", json={"home_id": str(home_id), "seconds": 60})
    assert r.status_code == 202, r.text
    rows = [r for r in await _audit_rows("zigbee_permit_join", since) if r.detail.get("home") == str(home_id)]
    assert rows and rows[-1].detail["seconds"] == 60
