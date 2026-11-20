"""Herstelmodus van de API (S-P3-700; ADR-0004 besluit 9; EX15).

Treft ``shc-api serve`` een database van een nieuwere my_house-versie aan (een revisie die niet in de eigen
migratieketen staat, ``backup.schema_relation`` = "newer"), dan start in plaats van de gewone app deze kleine
ASGI-app. Dat is de vlag op procesniveau: de app kent geen database, geen sessies, geen achtergrondtaken (hub, watch)
en geen auditregels, dus hij kan niets schrijven. Het proces stopt ook niet, dus er is geen herstartlus.

- ``GET /api/v1/health``: 503 met ``status: "herstelmodus"`` en de vaste Nederlandse uitleg; een healthcheck ziet de
  API dus niet als gezond.
- ``GET /api/v1/system/recovery`` (herstelinfo): 200 met dezelfde uitleg, de revisie van de database en de nieuwste
  revisie die deze versie kent. Zonder aanmelding; er staan geen gegevens van het huis in.
- Al het andere (elke methode, elke route): 503 ``database_newer``.
- Een websocket wordt eerst geaccepteerd en dan meteen gesloten met sluitcode 1013 ("Try Again Later") en reden
  ``database_newer``; er gaat geen bericht over. Een sluiting vóór de accept maakt uvicorn tot een HTTP 403 bij de
  handshake, en dan ziet de client code noch reden.

Nooit automatisch downgraden: de gebruiker kiest in het formulier van de Custom App de bijpassende versie of zet de
snapshot van vóór de update op aparte opslag terug. Bij terugval bereidt de huidige
``shc-api prepare-connector-fallback`` die offline kopie voor voordat de oude worker start.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.websockets import WebSocket

from .cli import DATABASE_NIEUWER_CODE, DATABASE_NIEUWER_TEKST
from .config import VERSION

HEALTH_PATH = "/api/v1/health"
RECOVERY_PATH = "/api/v1/system/recovery"
WS_TRY_AGAIN_LATER = 1013  # websocket-sluitcode "Try Again Later"
_HEADERS = (
    (b"content-type", b"application/json; charset=utf-8"),
    (b"x-content-type-options", b"nosniff"),
    (b"cache-control", b"no-store"),
    (b"referrer-policy", b"same-origin"),
)


class HerstelApp:
    """ASGI-app van de herstelmodus; vaste antwoorden, geen database."""

    def __init__(self, database_revision: str, newest_known: str) -> None:
        self.database_revision = database_revision
        self.newest_known = newest_known

    @staticmethod
    def _detail() -> dict[str, str]:
        return {"code": DATABASE_NIEUWER_CODE, "message": DATABASE_NIEUWER_TEKST}

    def answer(self, method: str, path: str) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path == HEALTH_PATH:
            return 503, {"status": "herstelmodus", "version": VERSION, "detail": self._detail()}
        if method == "GET" and path == RECOVERY_PATH:
            return 200, {
                "mode": "herstelmodus",
                **self._detail(),
                "version": VERSION,
                "database_revision": self.database_revision,
                "newest_known_revision": self.newest_known,
            }
        if method == "GET" and path == "/readyz":
            from .contract_http import problem

            return 503, problem(DATABASE_NIEUWER_CODE, DATABASE_NIEUWER_TEKST)
        return 503, {"detail": self._detail()}

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if kind == "websocket":
            ws = WebSocket(scope, receive, send)
            await ws.accept()  # zonder accept wordt de sluiting een HTTP 403 bij de handshake (zie boven)
            await ws.close(code=WS_TRY_AGAIN_LATER, reason=DATABASE_NIEUWER_CODE)
            return
        status, body = self.answer(scope.get("method", ""), scope.get("path", ""))
        raw = json.dumps(body, ensure_ascii=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [*_HEADERS, (b"content-length", str(len(raw)).encode())]
                + ([(b"retry-after", b"5")] if status == 503 else []),
            }
        )
        await send({"type": "http.response.body", "body": raw})
