"""Realtime events between worker and API via PostgreSQL LISTEN/NOTIFY, fanned out to WebSockets.

No extra broker needed; works the same on TrueNAS and any Linux host.

Besides the events forwarded to browsers and phones there is one internal event, ``auth.revoked``:
every API process's hub handles it itself (it is never forwarded) and closes the WebSockets whose
web session or phone token was revoked (logout, password change, user disabled/deleted, phone
revoked). A periodic re-check closes sockets whose session expired or whose revocation event was
missed (e.g. while the LISTEN connection was down)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
from fastapi import WebSocket
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings

CHANNEL = "shc_events"
AUTH_REVOKED = "auth.revoked"
# WebSocket close codes (documented in docs/api.md): 4401 = log in again, 4403 = phone access revoked.
CLOSE_AUTH_REQUIRED = 4401
CLOSE_REVOKED = 4403
RECHECK_INTERVAL_S = 60.0
log = logging.getLogger(__name__)

# The one shared list of realtime message types the server sends over the WebSocket, as
# ``{"type": ..., "home_id": ..., "data": ...}`` (verbeterlijst 36). docs/api.md describes the same
# list; tests/test_message_types.py checks that every type here is really published somewhere,
# that nothing is published that is not listed here, and that the documentation names no others.
MESSAGE_TYPES: dict[str, str] = {
    "zone.updated": "Zone: after a change, a new decision or a confirmed command",
    "lights.updated": "LightGroup: after a new state of a light or group",
    "energy.live": "LivePower: after a newer power reading (at most every 5 s per home)",
    "alert.created": "Alert: a new open alert",
    "alert.resolved": "{id}: an alert was resolved (remove it from the list)",
    "status.updated": "Overview.status: an integration, the worker, the backups, maintenance or safe mode changed",
    "command.updated": "{id, status, detail}: outcome of a command (confirmed, failed, denied ...)",
    "command.queued": "{id}: a command waits for the worker (also wakes the worker)",
    "integration.changed": "{id}: an integration was added, changed or removed (also wakes the worker)",
    "refresh": "{reason}: the message was too large to send; fetch everything again",
}
# Handled by every API process itself, never forwarded to clients.
INTERNAL_TYPES: dict[str, str] = {AUTH_REVOKED: "close the WebSockets of revoked sessions/tokens"}


def _default(o: Any) -> Any:
    if isinstance(o, uuid.UUID):
        return str(o)
    if hasattr(o, "isoformat"):
        return o.isoformat().replace("+00:00", "Z")
    raise TypeError(type(o))


def dumps(o: Any) -> str:
    return json.dumps(o, default=_default, separators=(",", ":"))


async def publish(db: AsyncSession, type_: str, home_id: uuid.UUID | None, data: Any) -> None:
    """Queue a NOTIFY in the current transaction (delivered on commit)."""
    payload = dumps({"type": type_, "home_id": home_id, "data": data})
    if len(payload) > 7900:  # NOTIFY limit is 8000 bytes; clients refetch on this hint
        payload = dumps({"type": "refresh", "home_id": home_id, "data": {"reason": type_}})
    await db.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": payload})


async def publish_auth_revoked(
    db: AsyncSession,
    *,
    reason: str,
    user_id: uuid.UUID | None = None,
    sessions: bool = False,
    clients: bool = False,
    keep_session: str | None = None,
    keep_client: uuid.UUID | None = None,
    session_ids: Iterable[str] = (),
    client_ids: Iterable[uuid.UUID] = (),
) -> None:
    """Tell every API process to close the WebSockets of revoked credentials (delivered on commit).

    ``session_ids`` are session row ids (sha256 of the cookie, never the cookie itself); ``sessions`` /
    ``clients`` select all web sessions / phone tokens of ``user_id`` except ``keep_session`` /
    ``keep_client`` (the credential doing the change)."""
    data = {
        "reason": reason,
        "user_id": user_id,
        "sessions": sessions,
        "clients": clients,
        "keep_session": keep_session,
        "keep_client": keep_client,
        "session_ids": list(session_ids)[:50],
        "client_ids": list(client_ids)[:50],
    }
    await publish(db, AUTH_REVOKED, None, data)


@dataclass(slots=True)
class SocketInfo:
    """Who is behind a WebSocket: used to close it when that credential is revoked."""

    home_id: str | None
    user_id: str
    via: str  # "cookie" | "token"
    session_id: str | None = None  # Session.id (sha256 of the cookie) for cookie auth
    client_id: str | None = None  # ApiClient.id for token auth

    def matches(self, d: dict[str, Any]) -> bool:
        if self.session_id and self.session_id in (d.get("session_ids") or ()):
            return True
        if self.client_id and self.client_id in [str(c) for c in d.get("client_ids") or ()]:
            return True
        if d.get("user_id") is None or str(d["user_id"]) != self.user_id:
            return False
        if self.via == "cookie":
            return bool(d.get("sessions")) and self.session_id != d.get("keep_session")
        return bool(d.get("clients")) and self.client_id != (str(d["keep_client"]) if d.get("keep_client") else None)


class Hub:
    def __init__(self) -> None:
        self.clients: dict[WebSocket, SocketInfo] = {}
        self._conn: asyncpg.Connection | None = None
        self._task: asyncio.Task | None = None
        self._recheck_task: asyncio.Task | None = None
        self.ready = asyncio.Event()  # set once LISTEN is active (tests wait on it)

    async def start(self) -> None:
        self.ready = asyncio.Event()
        self._task = asyncio.create_task(self._run())
        self._recheck_task = asyncio.create_task(self._recheck_loop())

    async def stop(self) -> None:
        for task in (self._task, self._recheck_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._task = self._recheck_task = None
        if self._conn:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None

    def register(self, ws: WebSocket, info: SocketInfo) -> None:
        self.clients[ws] = info

    def unregister(self, ws: WebSocket) -> None:
        self.clients.pop(ws, None)

    async def _run(self) -> None:
        dsn = get_settings().database_url.replace("+asyncpg", "")
        backoff = 1.0
        while True:
            try:
                self._conn = await asyncpg.connect(dsn)
                await self._conn.add_listener(CHANNEL, self._on_notify)
                self.ready.set()
                backoff = 1.0
                while not self._conn.is_closed():
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # reconnect with backoff
                log.warning("LISTEN-verbinding verbroken: %s", exc)
            self.ready.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        asyncio.get_running_loop().create_task(self.broadcast_raw(payload))

    async def broadcast_raw(self, payload: str) -> None:
        try:
            msg = json.loads(payload)
        except ValueError:
            return
        if msg.get("type") == AUTH_REVOKED:
            await self.revoke(msg.get("data") or {})
            return
        home = msg.get("home_id")
        out = dumps({"type": msg["type"], "data": msg["data"]})
        for ws, info in list(self.clients.items()):
            if home and info.home_id and info.home_id != home:
                continue
            try:
                await ws.send_text(out)
            except Exception:
                self.clients.pop(ws, None)

    async def close_socket(self, ws: WebSocket, info: SocketInfo, reason: str) -> None:
        """Send the error message first, then close: 4403 for a phone token, 4401 for a web session."""
        self.clients.pop(ws, None)
        if info.via == "token":
            code, err = CLOSE_REVOKED, "token_revoked"
        else:
            code, err = CLOSE_AUTH_REQUIRED, "auth_required"
        with contextlib.suppress(Exception):
            await ws.send_text(dumps({"type": "error", "data": {"code": err, "reason": reason}}))
        with contextlib.suppress(Exception):
            await ws.close(code=code)

    async def revoke(self, data: dict[str, Any]) -> int:
        """Handle an ``auth.revoked`` event: close every matching socket. Returns how many."""
        reason = str(data.get("reason") or "revoked")
        victims = [(ws, info) for ws, info in list(self.clients.items()) if info.matches(data)]
        for ws, info in victims:
            await self.close_socket(ws, info, reason)
        return len(victims)

    async def recheck(self) -> int:
        """Close sockets whose session/token is no longer valid (expired, revoked, user disabled)."""
        items = list(self.clients.items())
        if not items:
            return 0
        from sqlalchemy import or_, select

        from .db import sessionmaker
        from .models import ApiClient, User
        from .models import Session as WebSession

        now = datetime.now(UTC)
        session_ids = {i.session_id for _, i in items if i.session_id}
        client_ids = {uuid.UUID(i.client_id) for _, i in items if i.client_id}
        valid_sessions: set[str] = set()
        valid_clients: set[str] = set()
        async with sessionmaker()() as db:
            if session_ids:
                rows = await db.execute(
                    select(WebSession.id)
                    .join(User, User.id == WebSession.user_id)
                    .where(WebSession.id.in_(session_ids), WebSession.expires_at > now, User.disabled.is_(False))
                )
                valid_sessions = set(rows.scalars())
            if client_ids:
                rows = await db.execute(
                    select(ApiClient.id)
                    .join(User, User.id == ApiClient.user_id)
                    .where(
                        ApiClient.id.in_(client_ids),
                        ApiClient.revoked_at.is_(None),
                        or_(ApiClient.expires_at.is_(None), ApiClient.expires_at > now),
                        User.disabled.is_(False),
                    )
                )
                valid_clients = {str(c) for c in rows.scalars()}
        closed = 0
        for ws, info in items:
            ok = info.session_id in valid_sessions if info.via == "cookie" else info.client_id in valid_clients
            if not ok and ws in self.clients:
                await self.close_socket(ws, info, "expired")
                closed += 1
        return closed

    async def _recheck_loop(self) -> None:
        while True:
            await asyncio.sleep(RECHECK_INTERVAL_S)
            try:
                await self.recheck()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Controle van live-verbindingen mislukt: %s", exc)


hub = Hub()
