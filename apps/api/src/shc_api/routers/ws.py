"""Live updates over WebSocket.

Authentication: the web session cookie (only when the Origin matches the address the request was
made to, against cross-site WebSocket hijacking), or as first message ``{"type":"auth","token":...}``
(phones). On failure the server first sends ``{"type":"error","data":{"code":...}}`` and then closes:
4401 = log in again (code ``auth_required``), 4403 = this phone's token was revoked or expired
(code ``token_revoked`` / ``token_expired``). Open connections are closed the same way when their
session or token is revoked later (see events.Hub)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from ..db import sessionmaker
from ..deps import SESSION_COOKIE, resolve_principal
from ..events import CLOSE_AUTH_REQUIRED, CLOSE_REVOKED, SocketInfo, dumps, hub
from .auth import user_view

router = APIRouter()
AUTH_TIMEOUT_S = 10.0
DEFAULT_PORTS = {"http": 80, "https": 443}


def _host_port(value: str) -> tuple[str, int | None] | None:
    try:
        u = urlsplit("//" + value.strip())
        port = u.port
    except ValueError:
        return None
    if not u.hostname:
        return None
    return u.hostname.lower(), port


def origin_allowed(websocket: WebSocket) -> bool:
    """Same-origin check including the port (verbeterlijst 4).

    The request side is the Host header (nginx passes ``$http_host``, port included) or the
    X-Forwarded-Host a proxy put in front of it, with the scheme as uvicorn derived it from
    X-Forwarded-Proto of the trusted proxy (``forwarded_allow_ips``). A browser page cannot set these
    headers on a WebSocket handshake, so accepting X-Forwarded-Host does not help a hijacking page.
    When neither side names a port, a scheme difference is tolerated (TLS ended in front of a proxy
    that does not pass the scheme on); explicit ports must always match."""
    origin = websocket.headers.get("origin")
    if not origin:
        return True  # not a browser; cookies are only a CSWSH risk with a browser
    try:
        o = urlsplit(origin)
        o_port_explicit = o.port
    except ValueError:
        return False
    if o.scheme not in DEFAULT_PORTS or not o.hostname:
        return False  # includes Origin: null
    o_host = o.hostname.lower()
    o_port = o_port_explicit or DEFAULT_PORTS[o.scheme]
    req_scheme = "https" if websocket.url.scheme in ("wss", "https") else "http"
    candidates = [websocket.headers.get("host") or ""]
    xfh = websocket.headers.get("x-forwarded-host")
    if xfh:
        candidates.append(xfh.split(",")[0])
    for cand in candidates:
        hp = _host_port(cand)
        if hp is None or hp[0] != o_host:
            continue
        host_port_explicit = hp[1]
        if (host_port_explicit or DEFAULT_PORTS[req_scheme]) == o_port:
            return True
        if host_port_explicit is None and o_port_explicit is None:
            return True
    return False


async def _deny(websocket: WebSocket, close_code: int, code: str, reason: str | None = None) -> None:
    data: dict = {"code": code}
    if reason:
        data["reason"] = reason
    with contextlib.suppress(Exception):
        await websocket.send_text(dumps({"type": "error", "data": data}))
    with contextlib.suppress(Exception):
        await websocket.close(code=close_code)


@router.websocket("/ws")
async def ws(websocket: WebSocket, home_id: str | None = None) -> None:
    cookie = websocket.cookies.get(SESSION_COOKIE)
    reason = None
    if cookie and not origin_allowed(websocket):
        cookie, reason = None, "origin_mismatch"
    if home_id is not None:
        try:
            home_id = str(uuid.UUID(home_id))
        except ValueError:
            home_id = None
    await websocket.accept()
    bearer = None
    if not cookie:
        try:
            first = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT_S))
            if isinstance(first, dict) and first.get("type") == "auth":
                bearer = str(first.get("token", ""))
        except (TimeoutError, ValueError, KeyError, RuntimeError, WebSocketDisconnect):
            await _deny(websocket, CLOSE_AUTH_REQUIRED, "auth_required", reason or "no_credentials")
            return
    try:
        async with sessionmaker()() as db:
            principal, _csrf = await resolve_principal(db, cookie, bearer)
    except HTTPException as exc:
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
        if code in ("token_revoked", "token_expired"):
            await _deny(websocket, CLOSE_REVOKED, code)
        else:
            await _deny(websocket, CLOSE_AUTH_REQUIRED, "auth_required", reason)
        return
    except Exception:
        await _deny(websocket, CLOSE_AUTH_REQUIRED, "auth_required", reason)
        return
    info = SocketInfo(
        home_id=home_id,
        user_id=str(principal.user.id),
        via=principal.via,
        session_id=principal.session_id,
        client_id=str(principal.client_id) if principal.client_id else None,
    )
    hub.register(websocket, info)
    try:
        await websocket.send_text(
            dumps(
                {
                    "type": "hello",
                    "data": {
                        "user": user_view(principal.user),
                        "via": principal.via,
                        "client_role": principal.client_role,
                        "can_control": principal.can_control,
                    },
                }
            )
        )
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unregister(websocket)
