from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from .config import VERSION, get_settings
from .db import dispose, sessionmaker
from .deps import UNSAFE, Admin
from .events import dumps, hub
from .models import AuditLog
from .routers import (
    auth,
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

API_PREFIX = "/api/v1"
# Restore uploads may be large (contract C6); everything else is small JSON.
UPLOAD_PREFIX = f"{API_PREFIX}/system/restore"
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=get_settings().log_level)
    get_settings().resolved_secret_key()  # fail fast with a clear message
    await hub.start()
    yield
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
        s = get_settings()
        limit = s.max_upload_bytes if scope.get("path", "").startswith(UPLOAD_PREFIX) else s.max_body_bytes
        for name, value in scope.get("headers") or ():
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await _send_json(
                        send, 400, {"detail": {"code": "validation_error", "message": "Ongeldige Content-Length."}}
                    )
                    return
                if declared > limit:
                    await _send_json(send, 413, {"detail": BodyTooLarge(limit).detail})
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
        except BodyTooLarge as exc:
            if started:
                raise
            await _send_json(send, 413, {"detail": exc.detail})


class AuditMiddleware:
    """One audit_log row for every successful mutating request by a logged-in user or phone:
    actor, method, route template (no ids in the template; they go in detail.params) and status.
    Request bodies are never logged (they may hold passwords). Domain events keep their own rows."""

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
        principal = (scope.get("state") or {}).get("principal")
        if principal is None:
            return  # login, setup, redeem: audited by the endpoint itself
        route = scope.get("route")
        template = getattr(route, "path", None) or scope.get("path", "")
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
    for r in (auth, users, pairing, homes, zones, profiles, lights, energy, devices, optimization, system, ws):
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

    # Pure ASGI middlewares (no BaseHTTPMiddleware: its task group would wrap the 413 raised while
    # reading the body into a generic 400). Added last = outermost: the size limit applies first.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(BodyLimitMiddleware)
    return app


app = create_app()
