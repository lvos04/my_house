from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from ..config import get_settings
from ..deps import CSRF_COOKIE, DB, SESSION_COOKIE, Auth
from ..errors import api_error
from ..models import Session as WebSession
from ..models import User
from ..security import (
    MIN_PASSWORD_LENGTH,
    dummy_verify,
    hash_password,
    new_token,
    token_hash,
    verify_password,
)
from ..store import audit

router = APIRouter(prefix="/auth", tags=["auth"])

_attempts: dict[str, deque[float]] = defaultdict(deque)


def user_view(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role}


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.\-@]+$")
    password: str = Field(min_length=1, max_length=256)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _rate_limited(key: str) -> bool:
    s = get_settings()
    q = _attempts[key]
    now = time.monotonic()
    while q and now - q[0] > s.login_window_s:
        q.popleft()
    return len(q) >= s.login_max_attempts


async def _start_session(db: DB, user: User, response: Response) -> dict:
    s = get_settings()
    token = new_token()
    csrf = secrets.token_urlsafe(24)
    db.add(
        WebSession(
            id=token_hash(token),
            user_id=user.id,
            csrf_token=csrf,
            expires_at=datetime.now(UTC) + timedelta(hours=s.session_hours),
        )
    )
    await db.execute(delete(WebSession).where(WebSession.expires_at < datetime.now(UTC)))
    await db.commit()
    max_age = s.session_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=s.cookie_secure,
        samesite="strict",
        path="/",
    )
    return {"user": user_view(user), "csrf_token": csrf}


@router.get("/status")
async def status(request: Request, db: DB) -> dict:
    count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    user = None
    try:
        from ..deps import current_principal

        p = await current_principal(request, db)
        user = user_view(p.user)
    except Exception:  # not logged in
        user = None
    return {"setup_required": count == 0, "user": user}


@router.post("/setup")
async def setup(body: Credentials, response: Response, db: DB) -> dict:
    # Serialize concurrent setup attempts.
    await db.execute(select(func.pg_advisory_xact_lock(42_4242)))
    if (await db.execute(select(func.count()).select_from(User))).scalar_one() > 0:
        raise api_error(409, "setup_done", "Er bestaat al een beheerder. Log in.")
    if len(body.password) < MIN_PASSWORD_LENGTH:
        raise api_error(422, "validation_error", f"Wachtwoord moet minimaal {MIN_PASSWORD_LENGTH} tekens zijn.")
    user = User(username=body.username, password_hash=hash_password(body.password), role="admin")
    db.add(user)
    await db.flush()
    await audit(db, body.username, "setup_admin")
    return await _start_session(db, user, response)


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response, db: DB) -> dict:
    key = f"{_client_ip(request)}|{body.username.lower()}"
    if _rate_limited(key):
        raise api_error(429, "rate_limited", "Te veel pogingen. Probeer het over een paar minuten opnieuw.")
    user = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if user is None:
        dummy_verify()
    if user is None or user.disabled or not verify_password(user.password_hash, body.password):
        _attempts[key].append(time.monotonic())
        await audit(db, body.username, "login_failed", ip=_client_ip(request))
        await db.commit()
        raise api_error(401, "auth_required", "Gebruikersnaam of wachtwoord klopt niet.")
    _attempts.pop(key, None)
    await audit(db, user.username, "login", ip=_client_ip(request))
    return await _start_session(db, user, response)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: DB, p: Auth) -> Response:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        await db.execute(delete(WebSession).where(WebSession.id == token_hash(cookie)))
        await db.commit()
    response.status_code = 204
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@router.get("/me")
async def me(p: Auth) -> dict:
    return user_view(p.user)


class NewUser(Credentials):
    role: str = Field(pattern="^(admin|viewer)$")


@router.post("/users")
async def create_user(body: NewUser, db: DB, p: Auth) -> dict:
    if not p.is_admin:
        raise api_error(403, "forbidden", "Alleen een beheerder mag gebruikers toevoegen.")
    if len(body.password) < MIN_PASSWORD_LENGTH:
        raise api_error(422, "validation_error", f"Wachtwoord moet minimaal {MIN_PASSWORD_LENGTH} tekens zijn.")
    u = User(username=body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    await audit(db, p.actor, "user_created", body.username, role=body.role)
    await db.commit()
    return user_view(u)
