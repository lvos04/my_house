from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .errors import api_error
from .models import ApiClient, Home, User
from .models import Session as WebSession
from .security import TOKEN_PREFIX, constant_eq, token_hash

SESSION_COOKIE = "shc_session"
CSRF_COOKIE = "shc_csrf"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}

DB = Annotated[AsyncSession, Depends(get_session)]


@dataclass(slots=True)
class Principal:
    user: User
    via: str  # "cookie" | "token"
    client_id: uuid.UUID | None = None

    @property
    def actor(self) -> str:
        return f"{self.user.username}" + (f" (app {self.client_id})" if self.client_id else "")

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"


async def resolve_principal(db: AsyncSession, cookie: str | None, bearer: str | None) -> tuple[Principal, str | None]:
    """Return principal and (for cookie auth) the session CSRF token."""
    now = datetime.now(UTC)
    if bearer and bearer.startswith(TOKEN_PREFIX):
        row = (
            await db.execute(
                select(ApiClient, User)
                .join(User, User.id == ApiClient.user_id)
                .where(ApiClient.token_hash == token_hash(bearer), ApiClient.revoked_at.is_(None))
            )
        ).first()
        if row and not row.User.disabled:
            client: ApiClient = row.ApiClient
            if not client.last_used_at or now - client.last_used_at > timedelta(minutes=5):
                await db.execute(update(ApiClient).where(ApiClient.id == client.id).values(last_used_at=now))
                await db.commit()
            return Principal(row.User, "token", client.id), None
    if cookie:
        row = (
            await db.execute(
                select(WebSession, User)
                .join(User, User.id == WebSession.user_id)
                .where(WebSession.id == token_hash(cookie), WebSession.expires_at > now)
            )
        ).first()
        if row and not row.User.disabled:
            return Principal(row.User, "cookie"), row.Session.csrf_token
    raise api_error(401, "auth_required", "Log in om verder te gaan.")


async def current_principal(request: Request, db: DB) -> Principal:
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    principal, csrf = await resolve_principal(db, request.cookies.get(SESSION_COOKIE), bearer)
    if principal.via == "cookie" and request.method in UNSAFE:
        sent = request.headers.get("x-csrf-token", "")
        if not csrf or not sent or not constant_eq(sent, csrf):
            raise api_error(403, "csrf_failed", "Beveiligingstoken ontbreekt of klopt niet. Herlaad de pagina.")
    request.state.principal = principal
    return principal


Auth = Annotated[Principal, Depends(current_principal)]


async def require_admin(p: Auth) -> Principal:
    if not p.is_admin:
        raise api_error(403, "forbidden", "Alleen een beheerder mag dit doen.")
    return p


Admin = Annotated[Principal, Depends(require_admin)]


async def get_home(db: AsyncSession, home_id: uuid.UUID) -> Home:
    home = await db.get(Home, home_id)
    if not home:
        raise api_error(404, "not_found", "Woning niet gevonden.")
    return home
