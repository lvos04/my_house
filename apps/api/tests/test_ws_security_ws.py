"""Security stream: WebSocket origin check, close codes and revocation (verbeterlijst 4, 18)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import update
from starlette.websockets import WebSocket

from shc_api.db import sessionmaker
from shc_api.events import SocketInfo, dumps, hub
from shc_api.models import Session as WebSession
from shc_api.routers import ws as ws_router
from shc_api.routers.ws import origin_allowed
from shc_api.security import token_hash
from shc_api.store import set_setting

from .conftest import Session

PW = "een-lang-wachtwoord-ws1"


class FakeWS:
    """Minimal in-process WebSocket client for an ASGI app (same event loop as the database pool)."""

    def __init__(self, app, *, query="", headers=None, scheme="ws", client=("127.0.0.1", 50000)):
        self.app = app
        self.to_app: asyncio.Queue = asyncio.Queue()
        self.from_app: asyncio.Queue = asyncio.Queue()
        hdrs = {"host": "test", **(headers or {})}
        self.scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": scheme,
            "path": "/api/v1/ws",
            "raw_path": b"/api/v1/ws",
            "root_path": "",
            "query_string": query.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in hdrs.items()],
            "client": client,
            "server": ("test", 80),
            "subprotocols": [],
            "state": {},
        }
        self.closed_code: int | None = None
        self.task: asyncio.Task | None = None

    async def _receive(self):
        return await self.to_app.get()

    async def _send(self, message):
        if message["type"] == "websocket.close":
            self.closed_code = message.get("code", 1000)
            await self.to_app.put({"type": "websocket.disconnect", "code": self.closed_code})
        await self.from_app.put(message)

    async def connect(self):
        self.task = asyncio.create_task(self.app(self.scope, self._receive, self._send))
        await self.to_app.put({"type": "websocket.connect"})
        msg = await self.next()
        assert msg["type"] == "websocket.accept", msg
        return self

    async def next(self, wait_s=3.0):
        return await asyncio.wait_for(self.from_app.get(), wait_s)

    async def json(self, wait_s=3.0):
        msg = await self.next(wait_s)
        assert msg["type"] == "websocket.send", msg
        return json.loads(msg["text"])

    async def send(self, obj):
        await self.to_app.put({"type": "websocket.receive", "text": obj if isinstance(obj, str) else json.dumps(obj)})

    async def expect_close(self, code, error_code=None, wait_s=3.0):
        if error_code is not None:
            first = await self.json(wait_s)
            assert first["type"] == "error" and first["data"]["code"] == error_code, first
        msg = await self.next(wait_s)
        assert msg["type"] == "websocket.close" and msg.get("code") == code, msg
        await asyncio.wait_for(self.task, wait_s)

    async def nothing(self, wait=0.2) -> bool:
        await asyncio.sleep(wait)
        return self.from_app.empty()

    async def close(self):
        if self.task and not self.task.done():
            await self.to_app.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(self.task, 3)


def _client(app, ip="127.0.0.1"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 40003)), base_url="http://test")


async def _user(admin, username, role="admin"):
    r = await admin.post("/users", json={"username": username, "password": PW, "role": role})
    assert r.status_code == 200, r.text


async def _login(c, username) -> tuple[Session, str]:
    r = await c.post("/api/v1/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return Session(c, r.json()["csrf_token"]), c.cookies.get("shc_session")


async def _phone(admin, app, name) -> tuple[str, str]:
    async with sessionmaker()() as db:
        await set_setting(db, "lan_url", "http://192.168.1.20:8088")
        await db.commit()
    r = await admin.post("/pairing/codes", json={"label": name})
    code = json.loads(r.json()["qr_payload"])["code"]
    async with _client(app) as phone:
        r = await phone.post("/api/v1/pairing/redeem", json={"code": code, "device_name": name})
    return r.json()["token"], r.json()["client"]["id"]


def _cookie_ws(app, cookie, origin, host, **kw):
    headers = {"host": host, "cookie": f"shc_session={cookie}"}
    if origin:
        headers["origin"] = origin
    headers.update(kw.pop("headers", {}))
    return FakeWS(app, headers=headers, **kw)


@pytest.fixture
def fast_auth_timeout(monkeypatch):
    monkeypatch.setattr(ws_router, "AUTH_TIMEOUT_S", 0.2)


# --- origin check (item 4) ------------------------------------------------------------------


def _ws_obj(headers, scheme="ws"):
    scope = {
        "type": "websocket",
        "scheme": scheme,
        "path": "/api/v1/ws",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "server": ("api", 8000),
    }
    return WebSocket(scope, receive=None, send=None)


@pytest.mark.parametrize(
    ("origin", "headers", "scheme", "ok"),
    [
        ("http://192.168.1.20:8088", {"host": "192.168.1.20:8088"}, "ws", True),
        ("http://nas.local:8088", {"host": "NAS.local:8088"}, "ws", True),
        # nginx used to drop the port (Host $host): now a mismatch is a mismatch.
        ("http://192.168.1.20:8088", {"host": "192.168.1.20"}, "ws", False),
        ("http://192.168.1.20:9999", {"host": "192.168.1.20:8088"}, "ws", False),
        ("https://evil.example", {"host": "192.168.1.20:8088"}, "ws", False),
        ("null", {"host": "192.168.1.20:8088"}, "ws", False),
        # Through the tunnel: uvicorn turned X-Forwarded-Proto https into wss.
        ("https://huis.voorbeeld.nl", {"host": "huis.voorbeeld.nl"}, "wss", True),
        ("https://huis.voorbeeld.nl", {"host": "huis.voorbeeld.nl:443"}, "wss", True),
        ("https://huis.voorbeeld.nl", {"host": "huis.voorbeeld.nl:80"}, "ws", False),
        # Default ports on both sides, scheme not passed on by a proxy: tolerated.
        ("https://huis.voorbeeld.nl", {"host": "huis.voorbeeld.nl"}, "ws", True),
        # A proxy that rewrites Host but passes X-Forwarded-Host.
        ("http://192.168.1.20:8088", {"host": "api:8000", "x-forwarded-host": "192.168.1.20:8088"}, "ws", True),
        ("http://192.168.1.20:8088", {"host": "api:8000", "x-forwarded-host": "192.168.1.20"}, "ws", False),
    ],
)
def test_origin_check_compares_host_and_port(origin, headers, scheme, ok):
    assert origin_allowed(_ws_obj({"origin": origin, **headers}, scheme)) is ok


def test_no_origin_header_is_not_a_browser():
    assert origin_allowed(_ws_obj({"host": "192.168.1.20:8088"})) is True


# --- connection auth and close codes ------------------------------------------------------------


async def test_cookie_ws_with_matching_origin_and_port(app, admin, home):
    await _user(admin, "sec_ws_cookie", role="viewer")
    async with _client(app) as c:
        _s, cookie = await _login(c, "sec_ws_cookie")
    ws = await _cookie_ws(
        app, cookie, "http://192.168.1.20:8088", "192.168.1.20:8088", query=f"home_id={home['id']}"
    ).connect()
    hello = await ws.json()
    assert hello["type"] == "hello" and hello["data"]["user"]["username"] == "sec_ws_cookie"
    assert hello["data"]["via"] == "cookie" and hello["data"]["can_control"] is False
    info = next(i for w, i in hub.clients.items() if i.user_id == hello["data"]["user"]["id"])
    assert info.home_id == home["id"] and info.session_id == token_hash(cookie)
    await ws.send("ping")
    assert (await ws.next())["text"] == "pong"
    await ws.close()
    assert all(i.user_id != hello["data"]["user"]["id"] for i in hub.clients.values())


async def test_origin_mismatch_and_missing_auth_close_4401_with_error_first(app, admin, fast_auth_timeout):
    await _user(admin, "sec_ws_origin", role="viewer")
    async with _client(app) as c:
        _s, cookie = await _login(c, "sec_ws_origin")
    ws = await _cookie_ws(app, cookie, "http://192.168.1.20:8088", "192.168.1.20").connect()
    first = await ws.json()
    assert first == {"type": "error", "data": {"code": "auth_required", "reason": "origin_mismatch"}}
    await ws.expect_close(4401)
    ws = await FakeWS(app).connect()  # no cookie, no auth message
    await ws.expect_close(4401, "auth_required")
    ws = await FakeWS(app).connect()
    await ws.send({"type": "auth", "token": "shc_bestaat-niet"})
    await ws.expect_close(4401, "auth_required")
    ws = await FakeWS(app).connect()
    await ws.send("geen json")
    await ws.expect_close(4401, "auth_required")


async def test_token_ws_and_revoked_token_closes_4403(app, admin, home):
    token, cid = await _phone(admin, app, "sec-ws-phone")
    ws = await FakeWS(app, headers={"origin": "https://elders.example"}).connect()  # tokens work from anywhere
    await ws.send({"type": "auth", "token": token})
    hello = await ws.json()
    assert hello["type"] == "hello" and hello["data"]["via"] == "token" and hello["data"]["client_role"] == "control"
    await ws.close()
    assert (await admin.delete(f"/clients/{cid}")).status_code == 204
    ws = await FakeWS(app).connect()
    await ws.send({"type": "auth", "token": token})
    await ws.expect_close(4403, "token_revoked")


# --- revocation of open connections (item 18) -------------------------------------------------


@pytest.fixture
async def listening_hub():
    await hub.start()
    await asyncio.wait_for(hub.ready.wait(), 5)
    yield hub
    await hub.stop()


async def test_open_sockets_closed_on_revoke_and_logout_via_notify(app, admin, home, listening_hub):
    token, cid = await _phone(admin, app, "sec-ws-live")
    phone_ws = await FakeWS(app).connect()
    await phone_ws.send({"type": "auth", "token": token})
    assert (await phone_ws.json())["type"] == "hello"
    await _user(admin, "sec_ws_live", role="viewer")
    async with _client(app) as c:
        s, cookie = await _login(c, "sec_ws_live")
        web_ws = await _cookie_ws(app, cookie, "http://test", "test").connect()
        assert (await web_ws.json())["type"] == "hello"
        # Admin revokes the phone: only the phone's socket closes, with 4403.
        assert (await admin.delete(f"/clients/{cid}")).status_code == 204
        await phone_ws.expect_close(4403, "token_revoked")
        assert await web_ws.nothing()
        # Logging out closes the web socket of that session with 4401.
        assert (await s.post("/auth/logout")).status_code == 204
        err = await web_ws.json()
        assert err["type"] == "error" and err["data"] == {"code": "auth_required", "reason": "logout"}
        await web_ws.expect_close(4401)


async def test_password_change_closes_other_sockets_only(app, admin, home, listening_hub):
    await _user(admin, "sec_ws_pw")
    async with _client(app) as a, _client(app) as b:
        sa, cookie_a = await _login(a, "sec_ws_pw")
        _sb, cookie_b = await _login(b, "sec_ws_pw")
        ws_a = await _cookie_ws(app, cookie_a, None, "test").connect()
        ws_b = await _cookie_ws(app, cookie_b, None, "test").connect()
        assert (await ws_a.json())["type"] == (await ws_b.json())["type"] == "hello"
        r = await sa.post("/auth/password", json={"old_password": PW, "new_password": "nieuw-wachtwoord-ws2"})
        assert r.status_code == 200
        err = await ws_b.json()
        assert err["data"] == {"code": "auth_required", "reason": "password_changed"}
        await ws_b.expect_close(4401)
        assert await ws_a.nothing()
        await ws_a.close()


async def test_revoked_event_is_internal_and_home_filter_still_works(app, admin, home):
    await _user(admin, "sec_ws_filter", role="viewer")
    async with _client(app) as c:
        _s, cookie = await _login(c, "sec_ws_filter")
    ws = await _cookie_ws(app, cookie, None, "test", query=f"home_id={home['id']}").connect()
    hello = await ws.json()
    other_home = "00000000-0000-0000-0000-000000000001"
    await hub.broadcast_raw(dumps({"type": "zone.updated", "home_id": other_home, "data": {"id": 1}}))
    assert await ws.nothing(0.05)
    await hub.broadcast_raw(dumps({"type": "zone.updated", "home_id": home["id"], "data": {"id": 2}}))
    assert await ws.json() == {"type": "zone.updated", "data": {"id": 2}}
    # Someone else's revocation: not forwarded, socket stays open.
    await hub.broadcast_raw(
        dumps({"type": "auth.revoked", "home_id": None, "data": {"user_id": other_home, "sessions": True}})
    )
    assert await ws.nothing(0.05)
    # This user's sessions revoked (e.g. account disabled on another API process): closed.
    await hub.broadcast_raw(
        dumps(
            {
                "type": "auth.revoked",
                "home_id": None,
                "data": {"user_id": hello["data"]["user"]["id"], "sessions": True, "reason": "user_disabled"},
            }
        )
    )
    err = await ws.json()
    assert err["data"] == {"code": "auth_required", "reason": "user_disabled"}
    await ws.expect_close(4401)


async def test_periodic_recheck_closes_expired_sessions(app, admin, home):
    await _user(admin, "sec_ws_expire", role="viewer")
    async with _client(app) as c:
        _s, cookie = await _login(c, "sec_ws_expire")
    ws = await _cookie_ws(app, cookie, None, "test").connect()
    assert (await ws.json())["type"] == "hello"
    async with _client(app) as c2:
        _s2, cookie2 = await _login(c2, "sec_ws_expire")
    keep = await _cookie_ws(app, cookie2, None, "test").connect()
    assert (await keep.json())["type"] == "hello"
    async with sessionmaker()() as db:
        await db.execute(
            update(WebSession)
            .where(WebSession.id == token_hash(cookie))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
    assert await hub.recheck() >= 1
    err = await ws.json()
    assert err["data"] == {"code": "auth_required", "reason": "expired"}
    await ws.expect_close(4401)
    assert await keep.nothing(0.05)
    await keep.close()


def test_socket_info_matching():
    web = SocketInfo(home_id=None, user_id="u1", via="cookie", session_id="s1")
    phone = SocketInfo(home_id=None, user_id="u1", via="token", client_id="c1")
    assert web.matches({"session_ids": ["s1"]}) and not phone.matches({"session_ids": ["s1"]})
    assert phone.matches({"client_ids": ["c1"]}) and not web.matches({"client_ids": ["c1"]})
    everything = {"user_id": "u1", "sessions": True, "clients": True}
    assert web.matches(everything) and phone.matches(everything)
    assert not web.matches({**everything, "keep_session": "s1"})
    assert not phone.matches({**everything, "keep_client": "c1"})
    assert not phone.matches({"user_id": "u1", "sessions": True, "clients": False})
    assert not web.matches({"user_id": "u2", "sessions": True})
