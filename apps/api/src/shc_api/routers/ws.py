from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import sessionmaker
from ..deps import SESSION_COOKIE, resolve_principal
from ..events import hub

router = APIRouter()


@router.websocket("/ws")
async def ws(websocket: WebSocket, home_id: str | None = None) -> None:
    # Same-origin check for cookie auth (CSWSH protection); token auth works from any origin.
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    cookie = websocket.cookies.get(SESSION_COOKIE)
    if cookie and origin and host and origin.split("://", 1)[-1] != host:
        cookie = None
    await websocket.accept()
    bearer = None
    if not cookie:
        try:
            first = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=10))
            if first.get("type") == "auth":
                bearer = str(first.get("token", ""))
        except (TimeoutError, ValueError, WebSocketDisconnect):
            await websocket.close(code=4401)
            return
    try:
        async with sessionmaker()() as db:
            await resolve_principal(db, cookie, bearer)
    except Exception:
        await websocket.send_text(json.dumps({"type": "error", "data": {"code": "auth_required"}}))
        await websocket.close(code=4401)
        return
    hub.clients[websocket] = home_id
    await websocket.send_text(json.dumps({"type": "hello", "data": {}}))
    try:
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.pop(websocket, None)
