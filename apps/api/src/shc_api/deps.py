from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .errors import api_error
from .events import publish_auth_revoked
from .models import ApiClient, Home, User
from .models import Session as WebSession
from .security import TOKEN_PREFIX, constant_eq, token_hash

SESSION_COOKIE = "shc_session"
CSRF_COOKIE = "shc_csrf"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
# Phone tokens expire; the app renews them with POST /clients/self/refresh (verbeterlijst 18).
CLIENT_TTL = timedelta(days=180)
CLIENT_ROLES = ("admin", "control", "viewer")

DB = Annotated[AsyncSession, Depends(get_session)]


@dataclass(slots=True)
class Principal:
    user: User
    via: str  # "cookie" | "token"
    client_id: uuid.UUID | None = None
    client_role: str | None = None  # ApiClient.role when via == "token"; any role but admin/control is read-only
    session_id: str | None = None  # Session.id (sha256 of the cookie) when via == "cookie"

    @property
    def actor(self) -> str:
        return f"{self.user.username}" + (f" (app {self.client_id})" if self.client_id else "")

    @property
    def is_admin(self) -> bool:
        """Full management rights: an admin user on the web session, or a phone paired with role admin."""
        return self.user.role == "admin" and (self.via == "cookie" or self.client_role == "admin")

    @property
    def can_control(self) -> bool:
        """May operate devices (climate, lights): admin user via web, or a phone with role admin/control."""
        return self.user.role == "admin" and (self.via == "cookie" or self.client_role in ("admin", "control"))


def _phone_gone(code: str) -> Exception:
    if code == "token_expired":
        return api_error(
            401,
            "token_expired",
            "De koppeling van deze telefoon is verlopen. Koppel opnieuw via een QR-code in de web-app.",
        )
    return api_error(
        401,
        "token_revoked",
        "Deze telefoon is niet meer gekoppeld. Koppel opnieuw via een QR-code in de web-app.",
    )


async def resolve_principal(db: AsyncSession, cookie: str | None, bearer: str | None) -> tuple[Principal, str | None]:
    """Return principal and (for cookie auth) the session CSRF token.

    A revoked or expired phone token raises 401 with code ``token_revoked`` / ``token_expired`` (the app
    then shows "koppel opnieuw" instead of "geen verbinding"); anything else that fails is
    ``auth_required``."""
    now = datetime.now(UTC)
    phone_error: str | None = None
    if bearer and bearer.startswith(TOKEN_PREFIX):
        row = (
            await db.execute(
                select(ApiClient, User)
                .join(User, User.id == ApiClient.user_id)
                .where(ApiClient.token_hash == token_hash(bearer))
            )
        ).first()
        if row and not row.User.disabled:
            client: ApiClient = row.ApiClient
            if client.revoked_at is not None:
                phone_error = "token_revoked"
            elif client.expires_at is not None and client.expires_at <= now:
                phone_error = "token_expired"
            else:
                if (
                    client.expires_at is None
                    or not client.last_used_at
                    or now - client.last_used_at > timedelta(minutes=5)
                ):
                    values: dict = {"last_used_at": now}
                    if client.expires_at is None:  # paired before tokens expired: start the clock now
                        values["expires_at"] = now + CLIENT_TTL
                    await db.execute(update(ApiClient).where(ApiClient.id == client.id).values(**values))
                    await db.commit()
                return Principal(row.User, "token", client.id, client.role), None
    if cookie:
        sid = token_hash(cookie)
        row = (
            await db.execute(
                select(WebSession, User)
                .join(User, User.id == WebSession.user_id)
                .where(WebSession.id == sid, WebSession.expires_at > now)
            )
        ).first()
        if row and not row.User.disabled:
            return Principal(row.User, "cookie", session_id=sid), row.Session.csrf_token
    if phone_error:
        raise _phone_gone(phone_error)
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


async def require_control(p: Auth) -> Principal:
    if not p.can_control:
        raise api_error(403, "forbidden", "Alleen-lezen toegang.")
    return p


Control = Annotated[Principal, Depends(require_control)]


async def require_phone(p: Auth) -> Principal:
    if p.via != "token" or p.client_id is None:
        raise api_error(403, "forbidden", "Dit kan alleen vanuit een gekoppelde telefoon.")
    return p


Phone = Annotated[Principal, Depends(require_phone)]


async def get_home(db: AsyncSession, home_id: uuid.UUID) -> Home:
    home = await db.get(Home, home_id)
    if not home:
        raise api_error(404, "not_found", "Woning niet gevonden.")
    return home


async def revoke_access(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    reason: str,
    sessions: bool = True,
    phones: bool = True,
    keep_session: str | None = None,
    keep_client: uuid.UUID | None = None,
) -> tuple[int, int]:
    """Log out the user's web sessions and/or revoke their phone tokens (except the credential making
    the change), and close their live connections in every API process once the caller commits.

    Returns (sessions ended, phones revoked)."""
    ended = revoked = 0
    if sessions:
        stmt = delete(WebSession).where(WebSession.user_id == user_id)
        if keep_session:
            stmt = stmt.where(WebSession.id != keep_session)
        ended = (await db.execute(stmt)).rowcount or 0
    if phones:
        stmt = (
            update(ApiClient)
            .where(ApiClient.user_id == user_id, ApiClient.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
        if keep_client:
            stmt = stmt.where(ApiClient.id != keep_client)
        revoked = (await db.execute(stmt)).rowcount or 0
    if sessions or phones:
        await publish_auth_revoked(
            db,
            reason=reason,
            user_id=user_id,
            sessions=sessions,
            clients=phones,
            keep_session=keep_session,
            keep_client=keep_client,
        )
    return ended, revoked
