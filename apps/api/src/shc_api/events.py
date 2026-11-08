"""Realtime events between worker and API via PostgreSQL LISTEN/NOTIFY, fanned out to WebSockets.

No extra broker needed; works the same on TrueNAS and any Linux host."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

import asyncpg
from fastapi import WebSocket
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings

CHANNEL = "shc_events"
log = logging.getLogger(__name__)


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


class Hub:
    def __init__(self) -> None:
        self.clients: dict[WebSocket, str | None] = {}
        self._conn: asyncpg.Connection | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self._conn:
            await self._conn.close()

    async def _run(self) -> None:
        dsn = get_settings().database_url.replace("+asyncpg", "")
        backoff = 1.0
        while True:
            try:
                self._conn = await asyncpg.connect(dsn)
                await self._conn.add_listener(CHANNEL, self._on_notify)
                backoff = 1.0
                while not self._conn.is_closed():
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # reconnect with backoff
                log.warning("LISTEN-verbinding verbroken: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        asyncio.get_running_loop().create_task(self.broadcast_raw(payload))

    async def broadcast_raw(self, payload: str) -> None:
        try:
            msg = json.loads(payload)
        except ValueError:
            return
        home = msg.get("home_id")
        out = dumps({"type": msg["type"], "data": msg["data"]})
        for ws, sub in list(self.clients.items()):
            if home and sub and sub != home:
                continue
            try:
                await ws.send_text(out)
            except Exception:
                self.clients.pop(ws, None)


hub = Hub()
