"""Security stream: login transport and hardening (verbeterlijst 3, 16, API docs)."""

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from shc_api import security
from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import AuditLog, User
from shc_api.routers import auth
from shc_api.security import AttemptLimiter, lockout_seconds
from shc_api.store import set_setting

from .conftest import Session

PW = "een-lang-wachtwoord-123"


def _client(app, ip="127.0.0.1", base_url="http://test", wrap=None):
    transport = httpx.ASGITransport(app=wrap or app, client=(ip, 40000))
    return httpx.AsyncClient(transport=transport, base_url=base_url)


async def _user(admin, username, role="viewer", password=PW):
    r = await admin.post("/users", json={"username": username, "password": password, "role": role})
    assert r.status_code == 200, r.text
    return r.json()


async def _db_user(username) -> User:
    async with sessionmaker()() as db:
        return (await db.execute(select(User).where(User.username == username))).scalar_one()


def _cookie_flags(resp, name):
    for header in resp.headers.get_list("set-cookie"):
        if header.startswith(name + "="):
            return [p.strip().lower() for p in header.split(";")]
    raise AssertionError(f"cookie {name} not set")


# --- item 3: Secure cookie per request ---------------------------------------------------------


async def test_login_over_http_with_remote_url_configured(app, admin, monkeypatch):
    """At home over plain http, with a tunnel address configured, the session must still work."""
    monkeypatch.setattr(get_settings(), "cookie_secure", "auto")
    await _user(admin, "sec_http")
    async with sessionmaker()() as db:
        await set_setting(db, "remote_url", "https://huis.voorbeeld.nl")
        await db.commit()
    try:
        async with _client(app, base_url="http://nas.local:8088") as c:
            r = await c.post("/api/v1/auth/login", json={"username": "sec_http", "password": PW})
            assert r.status_code == 200, r.text
            assert "secure" not in _cookie_flags(r, "shc_session")
            assert "httponly" in _cookie_flags(r, "shc_session")
            assert "strict-transport-security" not in r.headers
            # The browser stored the cookie and sends it back over http: still logged in.
            assert (await c.get("/api/v1/auth/me")).status_code == 200
            s = Session(c, r.json()["csrf_token"])
            assert (await s.post("/auth/logout")).status_code == 204
    finally:
        async with sessionmaker()() as db:
            await set_setting(db, "remote_url", None)
            await db.commit()


async def test_https_from_trusted_proxy_sets_secure_cookie_and_real_ip(app, admin, monkeypatch):
    monkeypatch.setattr(get_settings(), "cookie_secure", "auto")
    await _user(admin, "sec_proxy")
    nginx = "172.30.251.2"
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=nginx)  # what uvicorn does with forwarded_allow_ips
    headers = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.7"}
    async with _client(app, ip=nginx, base_url="http://huis.voorbeeld.nl", wrap=wrapped) as c:
        r = await c.post("/api/v1/auth/login", json={"username": "sec_proxy", "password": PW}, headers=headers)
        assert r.status_code == 200, r.text
        assert "secure" in _cookie_flags(r, "shc_session") and "secure" in _cookie_flags(r, "shc_csrf")
        assert r.headers["strict-transport-security"].startswith("max-age=")
    async with sessionmaker()() as db:
        row = (
            (
                await db.execute(
                    select(AuditLog)
                    .where(AuditLog.actor == "sec_proxy", AuditLog.action == "login")
                    .order_by(AuditLog.id.desc())
                )
            )
            .scalars()
            .first()
        )
    assert row.detail["ip"] == "203.0.113.7"
    # The same headers from an untrusted peer are ignored: plain http, peer address.
    async with _client(app, ip="10.0.0.9", wrap=wrapped) as c:
        r = await c.post("/api/v1/auth/login", json={"username": "sec_proxy", "password": PW}, headers=headers)
        assert r.status_code == 200
        assert "secure" not in _cookie_flags(r, "shc_session")


# --- item 16: lockout per username, bounded per-IP map, Argon2 off the loop, body size ----------


def test_lockout_backoff_is_exponential_and_capped():
    assert [lockout_seconds(f, 5) for f in (0, 4, 5, 6, 7, 8, 9, 30)] == [0, 0, 60, 120, 240, 480, 900, 900]


def test_attempt_limiter_is_bounded_lru():
    lim = AttemptLimiter(max_attempts=2, window_s=60, max_keys=3)
    for key in ("a", "b", "c"):
        lim.hit(key)
    lim.hit("a")  # "a" becomes most recently used
    assert lim.blocked("a")
    lim.hit("d")  # evicts the least recently used key ("b")
    assert len(lim) == 3 and not lim.blocked("b")
    assert lim.retry_after("a") > 0
    for i in range(50_000):
        lim.hit(f"10.{i // 65536}.{i // 256 % 256}.{i % 256}")
    assert len(lim) == 3


async def test_per_username_lockout_in_database(app, admin):
    await _user(admin, "sec_lock")
    creds = {"username": "sec_lock", "password": "fout-wachtwoord"}
    async with _client(app, ip="198.51.100.10") as c:
        codes = [(await c.post("/api/v1/auth/login", json=creds)).status_code for _ in range(5)]
        assert codes == [401] * 5
        r = await c.post("/api/v1/auth/login", json={"username": "sec_lock", "password": PW})
        assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"
        assert 1 <= int(r.headers["retry-after"]) <= 60
    u = await _db_user("sec_lock")
    assert u.failed_logins == 5 and u.locked_until > datetime.now(UTC) + timedelta(seconds=50)
    # Another address does not help: the lock is per username.
    async with _client(app, ip="198.51.100.11") as c:
        assert (await c.post("/api/v1/auth/login", json={"username": "sec_lock", "password": PW})).status_code == 429
        async with sessionmaker()() as db:
            await db.execute(
                update(User).where(User.id == u.id).values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
            )
            await db.commit()
        # One more failure after the lock: doubled lock time.
        assert (await c.post("/api/v1/auth/login", json=creds)).status_code == 401
        u = await _db_user("sec_lock")
        assert u.failed_logins == 6 and u.locked_until > datetime.now(UTC) + timedelta(seconds=110)
        async with sessionmaker()() as db:
            await db.execute(update(User).where(User.id == u.id).values(locked_until=None))
            await db.commit()
        assert (await c.post("/api/v1/auth/login", json={"username": "sec_lock", "password": PW})).status_code == 200
    u = await _db_user("sec_lock")
    assert u.failed_logins == 0 and u.locked_until is None
    async with sessionmaker()() as db:
        locked = (
            (await db.execute(select(AuditLog).where(AuditLog.action == "user_locked", AuditLog.target == "sec_lock")))
            .scalars()
            .all()
        )
    assert len(locked) == 2
    auth._attempts.clear()


async def test_admin_can_unlock_a_user(app, admin):
    u = await _user(admin, "sec_unlock")
    async with sessionmaker()() as db:
        await db.execute(
            update(User)
            .where(User.username == "sec_unlock")
            .values(failed_logins=9, locked_until=datetime.now(UTC) + timedelta(minutes=10))
        )
        await db.commit()
    listed = next(x for x in (await admin.get("/users")).json() if x["id"] == u["id"])
    assert listed["locked"] is True and listed["failed_logins"] == 9
    r = await admin.c.patch(f"/api/v1/users/{u['id']}", json={"unlock": True}, headers=admin.h())
    assert r.status_code == 200 and r.json()["locked"] is False and r.json()["failed_logins"] == 0
    async with _client(app, ip="198.51.100.12") as c:
        assert (await c.post("/api/v1/auth/login", json={"username": "sec_unlock", "password": PW})).status_code == 200


async def test_per_ip_limit_covers_unknown_usernames(app):
    auth._attempts.clear()
    limit = get_settings().login_max_attempts * auth.IP_ATTEMPT_FACTOR
    async with _client(app, ip="198.51.100.50") as c:
        for i in range(limit):
            r = await c.post("/api/v1/auth/login", json={"username": f"onbekend{i}", "password": "x"})
            assert r.status_code == 401
        r = await c.post("/api/v1/auth/login", json={"username": "onbekend-extra", "password": "x"})
        assert r.status_code == 429 and "retry-after" in r.headers
    # Other addresses are not affected.
    async with _client(app, ip="198.51.100.51") as c:
        assert (await c.post("/api/v1/auth/login", json={"username": "onbekend", "password": "x"})).status_code == 401
    auth._attempts.clear()


async def test_argon2_runs_in_thread_and_saturation_gives_429(app, admin, monkeypatch):
    await _user(admin, "sec_busy")
    real = security.verify_password
    threads = []

    def slow_verify(stored, password):
        threads.append(threading.current_thread() is threading.main_thread())
        time.sleep(0.4)
        return real(stored, password)

    monkeypatch.setattr(security, "verify_password", slow_verify)

    async def attempt(i):
        async with _client(app, ip=f"198.51.100.{60 + i}") as c:
            return await c.post("/api/v1/auth/login", json={"username": "sec_busy", "password": PW})

    started = time.monotonic()
    ticks = 0

    async def ticker():  # the event loop keeps running while two hashes are in progress
        nonlocal ticks
        while time.monotonic() - started < 0.3:
            ticks += 1
            await asyncio.sleep(0.01)

    results = await asyncio.gather(attempt(0), attempt(1), attempt(2), ticker())
    codes = sorted(r.status_code for r in results[:3])
    assert codes == [200, 200, 429]
    busy = next(r for r in results[:3] if r.status_code == 429)
    assert busy.json()["detail"]["code"] == "rate_limited" and busy.headers["retry-after"] == "2"
    assert ticks >= 10
    assert threads == [False, False]  # Argon2 ran in worker threads, never on the event loop
    assert security._inflight == 0


async def test_body_size_limit_by_header_and_by_stream(app, monkeypatch):
    big = b'{"username":"x","password":"' + b"a" * 300_000 + b'"}'
    async with _client(app) as c:
        r = await c.post("/api/v1/auth/login", content=big, headers={"content-type": "application/json"})
        assert r.status_code == 413
        assert r.json()["detail"]["code"] == "payload_too_large"
        assert "te groot" in r.json()["detail"]["message"]

        async def chunks():
            for _ in range(4):
                yield b"a" * 100_000

        r = await c.post("/api/v1/auth/login", content=chunks(), headers={"content-type": "application/json"})
        assert r.status_code == 413 and r.json()["detail"]["code"] == "payload_too_large"
        # Restore uploads may be large: the size check lets them through (login is checked next).
        r = await c.post("/api/v1/system/restore/verify", content=b"x" * 300_000)
        assert r.status_code == 401
        monkeypatch.setattr(get_settings(), "max_upload_bytes", 1000)
        r = await c.post("/api/v1/system/restore/verify", content=b"x" * 2000)
        assert r.status_code == 413
        # Small requests are unaffected.
        r = await c.post("/api/v1/auth/login", json={"username": "onbekend-klein", "password": "x"})
        assert r.status_code == 401
    auth._attempts.clear()


# --- small item: API documentation not public ----------------------------------------------------


async def test_api_docs_only_for_admins(app, admin):
    async with _client(app) as c:
        assert (await c.get("/api/v1/docs")).status_code == 401
        assert (await c.get("/api/v1/openapi.json")).status_code == 401
        await _user(admin, "sec_docs_viewer")
        r = await c.post("/api/v1/auth/login", json={"username": "sec_docs_viewer", "password": PW})
        assert r.status_code == 200
        assert (await c.get("/api/v1/openapi.json")).status_code == 403
    r = await admin.get("/openapi.json")
    assert r.status_code == 200 and "/api/v1/users" in r.json()["paths"]
    r = await admin.get("/docs")
    assert r.status_code == 200 and "swagger" in r.text.lower()


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("http://192.168.1.20:8088", True),
        ("http://nas.local:8088", True),
        ("https://nas:8443", True),
        ("http://10.0.0.5", True),
        ("http://huis.voorbeeld.nl:8088", False),
        ("http://127.0.0.1:8088", False),
        ("http://8.8.8.8", False),
        ("ftp://192.168.1.20", False),
        ("http://192.168.1.20:8088/pad", False),
        ("http://user:pw@192.168.1.20", False),
    ],
)
def test_lan_url_rules(url, ok):
    assert (security.lan_url_problem(url) is None) is ok


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://huis.voorbeeld.nl", True),
        ("http://huis.voorbeeld.nl", False),
        ("https://192.168.1.20", False),
        ("https://nas.local", False),
        ("https://localhost", False),
    ],
)
def test_remote_url_rules(url, ok):
    assert (security.remote_url_problem(url) is None) is ok
