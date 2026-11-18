from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from asyncpg import CannotConnectNowError, InvalidCatalogNameError, PostgresConnectionError, TooManyConnectionsError
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from .config import VERSION, get_settings
from .contract_http import problem
from .db import dispose, sessionmaker
from .deps import UNSAFE, Admin
from .events import dumps, hub
from .logfilter import db_error_text, install_log_filter
from .models import AuditLog
from .routers import (
    auth,
    commissioning,
    devices,
    energy,
    homes,
    lights,
    optimization,
    pairing,
    profiles,
    system,
    users,
    ws,
    zones,
)
from .watch import watch

API_PREFIX = "/api/v1"
# Restore uploads may be large (contract C6); everything else is small JSON.
UPLOAD_PREFIX = f"{API_PREFIX}/system/restore"
COMMISSIONING_PREFIX = f"{API_PREFIX}{commissioning.router.prefix}"
CONTRACT_BODY_LIMIT = 1 << 20
log = logging.getLogger(__name__)


SLEUTEL_TE_KORT_TEKST = (
    "SHC_SECRET_KEY of SHC_SECRET_KEY_FILE is te kort (minimaal 32 tekens). Genereer er een met: "
    "python -c 'import secrets;print(secrets.token_urlsafe(48))'"
)


def check_secret_key() -> None:
    """Refuse to start without a usable master key. A missing key raises the RuntimeError of
    resolved_secret_key(); a key SecretBox refuses (too short) raises a RuntimeError of the same kind with a
    fixed Dutch text, never (part of) the key (KLEIN-42). Routes then never run with an unusable key."""
    from .security import SecretBox

    try:
        SecretBox(get_settings().resolved_secret_key())
    except ValueError:
        raise RuntimeError(SLEUTEL_TE_KORT_TEKST) from None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=get_settings().log_level)
    install_log_filter()  # vangnet: geen DETAIL van PostgreSQL in de log (KLEIN-37)
    check_secret_key()  # fail fast with a clear message (missing or too short)
    await hub.start()
    watch.start()  # worker heartbeat / backups: alert, status.updated, optional push (28, 36)
    yield
    await watch.stop()
    await hub.stop()
    await dispose()


def _human_bytes(n: int) -> str:
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("kB", 1 << 10)):
        if n >= size:
            value = n / size
            return f"{value:.0f} {unit}" if value >= 10 or value == int(value) else f"{value:.1f} {unit}"
    return f"{n} bytes"


class BodyTooLarge(HTTPException):
    """Raised from the wrapped ``receive``: FastAPI re-raises HTTPExceptions met while reading the body,
    so this turns into a 413 answer instead of a generic 400."""

    def __init__(self, limit: int) -> None:
        super().__init__(
            status_code=413,
            detail={
                "code": "payload_too_large",
                "message": f"Het verzoek is te groot (maximaal {_human_bytes(limit)}).",
            },
        )


async def _send_json(send: Any, status: int, body: dict, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    raw = dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
                (b"x-content-type-options", b"nosniff"),
                (b"cache-control", b"no-store"),
                (b"connection", b"close"),
                *(headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": raw})


class SecurityHeadersMiddleware:
    """Default response headers; HSTS only for requests that arrived over https (as uvicorn sees them
    after the trusted proxy's X-Forwarded-Proto, contract C5), never on the plain-http home address."""

    DEFAULTS = (
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"same-origin"),
        (b"cache-control", b"no-store"),
    )
    HSTS = (b"strict-transport-security", b"max-age=31536000")

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        extra = [*self.DEFAULTS, self.HSTS] if scope.get("scheme") == "https" else list(self.DEFAULTS)

        async def with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {k.lower() for k, _ in headers}
                headers += [(k, v) for k, v in extra if k not in present]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, with_headers)


class BodyLimitMiddleware:
    """Reject request bodies above ``max_body_bytes`` (restore uploads: ``max_upload_bytes``) before
    the app reads them: by Content-Length up front, and by counting the bytes actually received
    (chunked uploads, or a Content-Length that lies). Verbeterlijst 16, contract C6."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        contract = path == COMMISSIONING_PREFIX or path.startswith(COMMISSIONING_PREFIX + "/")
        s = get_settings()
        limit = s.max_upload_bytes if path.startswith(UPLOAD_PREFIX) else s.max_body_bytes
        if contract:
            limit = CONTRACT_BODY_LIMIT

        async def too_large() -> None:
            detail = BodyTooLarge(limit).detail
            body = problem(**detail, max_bytes=limit) if contract else {"detail": detail}
            await _send_json(send, 413, body)

        for name, value in scope.get("headers") or ():
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    detail = {"code": "validation_error", "message": "Ongeldige Content-Length."}
                    if contract:
                        await _send_json(send, 422, problem(**detail, fouten=[{"loc": ["header", "content-length"]}]))
                    else:
                        await _send_json(send, 400, {"detail": detail})
                    return
                if declared > limit:
                    await too_large()
                    return
        if contract:
            # Tel ook gestreamde bodies volledig vóór authenticatie en routering.
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    return
                part = message.get("body", b"")
                if len(body) + len(part) > limit:
                    await too_large()
                    return
                body.extend(part)
                if not message.get("more_body"):
                    break

            pending = True

            async def replay_body() -> dict:
                nonlocal pending
                if pending:
                    pending = False
                    return {**message, "body": bytes(body)}
                return await receive()

            await self.app(scope, replay_body, send)
            return
        received = 0
        started = False

        async def limited_receive() -> dict:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge(limit)
            return message

        async def tracking_send(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            if started:
                raise
            await too_large()


def route_template(scope: dict) -> str:
    """The matched route's path template with the API prefix, e.g. ``/api/v1/users/{user_id}``.

    FastAPI >= 0.16x includes routers lazily and puts the router's own route in ``scope["route"]``,
    whose path lacks the include prefix; older versions stored the prefixed copy. Both give the same
    result here. Without a matched route the raw path is used."""
    path = scope.get("path", "")
    template = getattr(scope.get("route"), "path", None)
    if not template:
        return path
    if path.startswith(API_PREFIX + "/") and not template.startswith(API_PREFIX + "/"):
        template = API_PREFIX + template
    return template


class AuditMiddleware:
    """Log een geslaagde mutatie van een aangemelde gebruiker of telefoon.

    Bewaar actor, methode, route, status en routeparameters. Handelingen die zelf een
    transactiegebonden auditregel schrijven kunnen de aanvullende verzoekregel overslaan.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method") not in UNSAFE:
            await self.app(scope, receive, send)
            return
        status = 0

        async def tracking_send(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        scope.setdefault("state", {})
        await self.app(scope, receive, tracking_send)
        if not 200 <= status < 400 or scope.get("path", "").startswith(UPLOAD_PREFIX):
            return  # restore replaces the database; it audits itself
        state = scope["state"]
        if state.get("domain_audit"):
            return  # Het domein heeft dezelfde handeling al in de mutatietransactie vastgelegd.
        principal = state.get("principal")
        if principal is None:
            return  # login, setup, redeem: audited by the endpoint itself
        template = route_template(scope)
        params = {k: str(v) for k, v in (scope.get("path_params") or {}).items()}
        try:
            async with sessionmaker()() as db:
                db.add(
                    AuditLog(
                        actor=principal.actor[:120],
                        action="request",
                        target=f"{scope['method']} {template}"[:200],
                        detail={"status": status, "params": params, "via": principal.via},
                    )
                )
                await db.commit()
        except Exception as exc:  # never fail a request because the audit row could not be written
            log.warning("Auditregel niet opgeslagen: %s", exc)


# Every router, included under API_PREFIX (tests enumerate routes through this, not app.routes).
ROUTERS = (
    auth, users, pairing, homes, zones, profiles, lights, energy, devices, commissioning, optimization, system, ws,
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Smart Home Control",
        version=VERSION,
        lifespan=lifespan,
        # API documentation only for logged-in admins (routes below), never public via the tunnel.
        docs_url=None,
        openapi_url=None,
        redoc_url=None,
    )
    for r in ROUTERS:
        app.include_router(r.router, prefix=API_PREFIX)

    @app.get(f"{API_PREFIX}/openapi.json", include_in_schema=False)
    async def _openapi(p: Admin) -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get(f"{API_PREFIX}/docs", include_in_schema=False)
    async def _docs(p: Admin) -> HTMLResponse:
        return get_swagger_ui_html(openapi_url=f"{API_PREFIX}/openapi.json", title="Smart Home Control – API")

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        msg = str(first.get("msg", "Ongeldige invoer")).removeprefix("Value error, ")
        loc = ".".join(str(x) for x in first.get("loc", [])[1:])
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "validation_error",
                    "message": f"{loc}: {msg}" if loc else msg,
                    "errors": [{k: v for k, v in e.items() if k in ("loc", "msg", "type")} for e in exc.errors()],
                }
            },
        )

    @app.exception_handler(DBAPIError)
    @app.exception_handler(PostgresConnectionError)
    @app.exception_handler(CannotConnectNowError)
    @app.exception_handler(TooManyConnectionsError)
    @app.exception_handler(InvalidCatalogNameError)
    @app.exception_handler(PoolTimeoutError)
    @app.exception_handler(OSError)
    async def _database_error(req: Request, exc: Exception) -> PlainTextResponse | JSONResponse:
        """Log soort, sqlstate, constraint en tabel (KLEIN-37).

        Inbedrijfstelling geeft een contractfout bij een onbereikbare database. De bestaande HTTP-routes
        behouden hun platte 500-antwoord. Websockets geven de uitzondering door.
        """
        if req.scope.get("type") != "http":
            raise exc
        route = route_template(req.scope)
        commissioning_route = route == COMMISSIONING_PREFIX or route.startswith(COMMISSIONING_PREFIX + "/")
        if not commissioning_route and not isinstance(exc, DBAPIError):
            raise exc
        log.error("Onverwachte databasefout bij %s %s: %s", req.method, route, db_error_text(exc))
        if commissioning_route:
            return JSONResponse(
                problem("dependency_unavailable", "De database is tijdelijk niet beschikbaar."),
                status_code=503,
                headers={"Retry-After": "5"},
            )
        return PlainTextResponse("Internal Server Error", status_code=500)

    # Pure ASGI middlewares (no BaseHTTPMiddleware: its task group would wrap the 413 raised while
    # reading the body into a generic 400). Added last = outermost: the size limit applies first.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(BodyLimitMiddleware)
    return app


app = create_app()
