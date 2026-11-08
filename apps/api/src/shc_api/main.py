from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import VERSION, get_settings
from .db import dispose
from .events import hub
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=get_settings().log_level)
    get_settings().resolved_secret_key()  # fail fast with a clear message
    await hub.start()
    yield
    await hub.stop()
    await dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Smart Home Control",
        version=VERSION,
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        redoc_url=None,
    )
    for r in (auth, users, pairing, homes, zones, profiles, lights, energy, devices, optimization, system, ws):
        app.include_router(r.router, prefix=API_PREFIX)

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

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    return app


app = create_app()
