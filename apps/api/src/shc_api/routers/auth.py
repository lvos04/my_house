from __future__ import annotations

import math
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from ..config import get_settings
from ..deps import CSRF_COOKIE, DB, SESSION_COOKIE, Auth, revoke_access
from ..errors import api_error
from ..events import publish_auth_revoked
from ..models import Session as WebSession
from ..models import User
from ..security import (
    MIN_PASSWORD_LENGTH,
    AttemptLimiter,
    HashBusy,
    dummy_verify_async,
    hash_password_async,
    lockout_seconds,
    new_token,
    token_hash,
    verify_password_async,
)
from ..store import audit

router = APIRouter(prefix="/auth", tags=["auth"])

# Failed logins per client IP (request.client.host: the real client, as uvicorn derives it from
# X-Forwarded-For of the trusted proxy only). Per-username lockout lives in the database
# (users.failed_logins / locked_until), this map only throttles one address trying many usernames.
IP_ATTEMPT_FACTOR = 4
_attempts = AttemptLimiter(
    get_settings().login_max_attempts * IP_ATTEMPT_FACTOR, get_settings().login_window_s, max_keys=10_000
)


def user_view(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role}


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.\-@]+$")
    password: str = Field(min_length=1, max_length=256)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _minutes(seconds: float) -> str:
    m = max(1, math.ceil(seconds / 60))
    return "1 minuut" if m == 1 else f"{m} minuten"


def too_many(seconds: float, message: str | None = None) -> HTTPException:
    secs = max(1, math.ceil(seconds))
    return HTTPException(
        status_code=429,
        detail={
            "code": "rate_limited",
            "message": message or f"Te veel mislukte pogingen. Probeer het over {_minutes(secs)} opnieuw.",
        },
        headers={"Retry-After": str(secs)},
    )


def server_busy() -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={
            "code": "rate_limited",
            "message": "De server is even druk. Probeer het over een paar seconden opnieuw.",
        },
        headers={"Retry-After": "2"},
    )


async def hash_new_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise api_error(422, "validation_error", f"Wachtwoord moet minimaal {MIN_PASSWORD_LENGTH} tekens zijn.")
    try:
        return await hash_password_async(password)
    except HashBusy:
        raise server_busy() from None


async def password_matches(db: DB, user: User, password: str, *, ip: str | None = None) -> bool:
    """Lockout-aware password check, used by login, password change and admin-role pairing.

    Raises 429 while the account is locked (without checking the password) or when all hashing slots
    are busy. A wrong password counts towards the lockout; the caller must commit."""
    now = datetime.now(UTC)
    if user.locked_until and user.locked_until > now:
        raise too_many((user.locked_until - now).total_seconds())
    try:
        ok = await verify_password_async(user.password_hash, password)
    except HashBusy:
        raise server_busy() from None
    if ok:
        if user.failed_logins or user.locked_until:
            await db.execute(update(User).where(User.id == user.id).values(failed_logins=0, locked_until=None))
            user.failed_logins, user.locked_until = 0, None
        return True
    failures = (
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(failed_logins=User.failed_logins + 1)
            .returning(User.failed_logins)
        )
    ).scalar_one()
    lock = lockout_seconds(failures, get_settings().login_max_attempts)
    if lock:
        until = now + timedelta(seconds=lock)
        await db.execute(update(User).where(User.id == user.id).values(locked_until=until))
        await audit(db, user.username, "user_locked", user.username, failures=failures, seconds=lock, ip=ip)
    return False


async def _start_session(db: DB, user: User, request: Request, response: Response) -> dict:
    s = get_settings()
    secure = s.cookie_secure_for(request.url.scheme)
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
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=secure,
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
async def setup(body: Credentials, request: Request, response: Response, db: DB) -> dict:
    # Serialize concurrent setup attempts.
    await db.execute(select(func.pg_advisory_xact_lock(42_4242)))
    if (await db.execute(select(func.count()).select_from(User))).scalar_one() > 0:
        raise api_error(409, "setup_done", "Er bestaat al een beheerder. Log in.")
    user = User(
        username=body.username,
        password_hash=await hash_new_password(body.password),
        role="admin",
        password_changed_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    await audit(db, body.username, "setup_admin", ip=client_ip(request))
    return await _start_session(db, user, request, response)


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response, db: DB) -> dict:
    ip = client_ip(request)
    if _attempts.blocked(ip):
        raise too_many(_attempts.retry_after(ip))
    user = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    ok = False
    if user is None or user.disabled:
        try:
            await dummy_verify_async()  # same cost as a real check: do not reveal which usernames exist
        except HashBusy:
            raise server_busy() from None
    else:
        ok = await password_matches(db, user, body.password, ip=ip)
    if not ok:
        _attempts.hit(ip)
        await audit(db, body.username, "login_failed", ip=ip)
        await db.commit()
        raise api_error(401, "auth_required", "Gebruikersnaam of wachtwoord klopt niet.")
    assert user is not None
    await audit(db, user.username, "login", ip=ip)
    return await _start_session(db, user, request, response)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: DB, p: Auth) -> Response:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        sid = token_hash(cookie)
        await db.execute(delete(WebSession).where(WebSession.id == sid))
        await publish_auth_revoked(db, reason="logout", session_ids=[sid])
        await db.commit()
    response.status_code = 204
    secure = get_settings().cookie_secure_for(request.url.scheme)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, httponly=True, samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, samesite="strict")
    return response


@router.get("/me")
async def me(p: Auth) -> dict:
    return {
        **user_view(p.user),
        "via": p.via,
        "client_role": p.client_role,
        "is_admin": p.is_admin,
        "can_control": p.can_control,
        "password_changed_at": p.user.password_changed_at,
    }


class PasswordChange(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)
    keep_phones: bool = False


@router.post("/password")
async def change_password(body: PasswordChange, request: Request, db: DB, p: Auth) -> dict:
    """Change your own password. Logs out all your other web sessions and revokes your paired phones
    (unless ``keep_phones``); the session or phone making the change stays signed in."""
    user = await db.get(User, p.user.id, with_for_update=True)
    assert user is not None
    if not await password_matches(db, user, body.old_password, ip=client_ip(request)):
        await db.commit()  # keep the failure count
        raise api_error(403, "wrong_password", "Je huidige wachtwoord klopt niet.")
    if body.new_password == body.old_password:
        raise api_error(422, "validation_error", "Kies een ander wachtwoord dan je huidige.")
    user.password_hash = await hash_new_password(body.new_password)
    user.password_changed_at = datetime.now(UTC)
    user.failed_logins, user.locked_until = 0, None
    ended, revoked = await revoke_access(
        db,
        user.id,
        reason="password_changed",
        sessions=True,
        phones=not body.keep_phones,
        keep_session=p.session_id,
        keep_client=p.client_id,
    )
    await audit(db, p.actor, "password_changed", user.username, sessions=ended, phones_revoked=revoked)
    await db.commit()
    return {"sessions_ended": ended, "phones_revoked": revoked}
