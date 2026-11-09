"""User management and the audit log (verbeterlijst 18).

User management needs an admin on a web session (``WebAdmin``): a phone token is refused, even one
paired with role admin, so a stolen phone cannot create a new admin before it is revoked. The audit
log may also be read from an admin phone. Changing your own password is ``POST /auth/password``
(routers/auth.py; it asks for the current password)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..deps import DB, Admin, Principal, WebAdmin, revoke_access
from ..errors import api_error, not_found
from ..models import ApiClient, AuditLog, User
from ..models import Session as WebSession
from ..store import audit
from .auth import Credentials, hash_new_password

router = APIRouter(tags=["users"])
ADMIN_LOCK = 42_4243  # advisory lock: serialize "keep at least one admin" checks


async def user_full(db: DB, u: User) -> dict:
    now = datetime.now(UTC)
    sessions = (
        await db.execute(
            select(func.count()).select_from(WebSession).where(WebSession.user_id == u.id, WebSession.expires_at > now)
        )
    ).scalar_one()
    phones = (
        await db.execute(
            select(func.count())
            .select_from(ApiClient)
            .where(
                ApiClient.user_id == u.id,
                ApiClient.revoked_at.is_(None),
                (ApiClient.expires_at.is_(None)) | (ApiClient.expires_at > now),
            )
        )
    ).scalar_one()
    locked = bool(u.locked_until and u.locked_until > now)
    return {
        "id": u.id,
        "username": u.username,
        "role": u.role,
        "disabled": u.disabled,
        "created_at": u.created_at,
        "password_changed_at": u.password_changed_at,
        "failed_logins": u.failed_logins,
        "locked_until": u.locked_until if locked else None,
        "locked": locked,
        "active_sessions": sessions,
        "active_phones": phones,
    }


@router.get("/users")
async def list_users(db: DB, p: WebAdmin) -> list[dict]:
    users = (await db.execute(select(User).order_by(User.created_at, User.username))).scalars().all()
    return [await user_full(db, u) for u in users]


class NewUser(Credentials):
    role: Literal["admin", "viewer"]


async def _create(body: NewUser, db: DB, p: Principal) -> dict:
    exists = (await db.execute(select(User.id).where(func.lower(User.username) == body.username.lower()))).first()
    if exists:
        raise api_error(409, "username_taken", "Deze gebruikersnaam bestaat al. Kies een andere naam.")
    u = User(
        username=body.username,
        password_hash=await hash_new_password(body.password),
        role=body.role,
        password_changed_at=datetime.now(UTC),
    )
    db.add(u)
    await db.flush()
    await audit(db, p.actor, "user_created", body.username, role=body.role)
    await db.commit()
    return await user_full(db, u)


@router.post("/users")
async def create_user(body: NewUser, db: DB, p: WebAdmin) -> dict:
    return await _create(body, db, p)


@router.post("/auth/users", deprecated=True)
async def create_user_legacy(body: NewUser, db: DB, p: WebAdmin) -> dict:
    """Old path, kept for existing clients; use ``POST /users``."""
    return await _create(body, db, p)


async def _target(db: DB, user_id: uuid.UUID) -> User:
    await db.execute(select(func.pg_advisory_xact_lock(ADMIN_LOCK)))
    u = await db.get(User, user_id, with_for_update=True)
    if not u:
        raise not_found("Gebruiker")
    return u


async def ensure_other_admin(db: DB, target: User) -> None:
    """Refuse when ``target`` is the last enabled admin (call under the ADMIN_LOCK advisory lock)."""
    if target.role != "admin" or target.disabled:
        return
    others = (
        await db.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.disabled.is_(False), User.id != target.id)
        )
    ).scalar_one()
    if others == 0:
        raise api_error(409, "last_admin", "Er moet minstens één actieve beheerder blijven.")


class UserPatch(BaseModel):
    role: Literal["admin", "viewer"] | None = None
    disabled: bool | None = None
    unlock: bool = False  # clear a lockout after too many failed logins


@router.patch("/users/{user_id}")
async def patch_user(user_id: uuid.UUID, body: UserPatch, db: DB, p: WebAdmin) -> dict:
    u = await _target(db, user_id)
    changes: dict = {}
    if body.role is not None and body.role != u.role:
        changes["role"] = body.role
    if body.disabled is not None and body.disabled != u.disabled:
        changes["disabled"] = body.disabled
    if changes and u.id == p.user.id:
        raise api_error(
            409,
            "self_change",
            "Je kunt je eigen rol niet wijzigen en jezelf niet uitschakelen. Laat een andere beheerder dit doen.",
        )
    if changes.get("role") == "viewer" or changes.get("disabled") is True:
        await ensure_other_admin(db, u)
    if "role" in changes:
        u.role = changes["role"]
    if "disabled" in changes:
        u.disabled = changes["disabled"]
    if body.unlock and (u.failed_logins or u.locked_until):
        u.failed_logins, u.locked_until = 0, None
        changes["unlocked"] = True
    if changes.get("disabled") is True:
        ended, revoked = await revoke_access(db, u.id, reason="user_disabled")
        changes.update(sessions_ended=ended, phones_revoked=revoked)
    if changes:
        await audit(db, p.actor, "user_updated", u.username, **changes)
    await db.commit()
    return await user_full(db, u)


class PasswordReset(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.post("/users/{user_id}/password")
async def reset_password(user_id: uuid.UUID, body: PasswordReset, db: DB, p: WebAdmin) -> dict:
    """Admin sets a new password for another user: all their web sessions end and their phones are
    revoked (they pair again with a QR code)."""
    u = await _target(db, user_id)
    if u.id == p.user.id:
        raise api_error(409, "self_change", "Wijzig je eigen wachtwoord via 'Wachtwoord wijzigen'.")
    u.password_hash = await hash_new_password(body.password)
    u.password_changed_at = datetime.now(UTC)
    u.failed_logins, u.locked_until = 0, None
    ended, revoked = await revoke_access(db, u.id, reason="password_reset")
    await audit(db, p.actor, "password_reset", u.username, sessions=ended, phones_revoked=revoked)
    await db.commit()
    return {"sessions_ended": ended, "phones_revoked": revoked}


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: uuid.UUID, db: DB, p: WebAdmin) -> Response:
    u = await _target(db, user_id)
    if u.id == p.user.id:
        raise api_error(409, "self_delete", "Je kunt je eigen account niet verwijderen.")
    await ensure_other_admin(db, u)
    ended, revoked = await revoke_access(db, u.id, reason="user_deleted")
    await audit(db, p.actor, "user_deleted", u.username, role=u.role, sessions=ended, phones_revoked=revoked)
    await db.delete(u)  # sessions, phones and open pairing codes go with it (ON DELETE CASCADE)
    await db.commit()
    return Response(status_code=204)


# --- audit log ------------------------------------------------------------------------------


def audit_view(a: AuditLog) -> dict:
    return {"id": a.id, "at": a.at, "actor": a.actor, "action": a.action, "target": a.target, "detail": a.detail}


@router.get("/audit")
async def audit_log(
    db: DB,
    p: Admin,
    limit: int = Query(100, ge=1, le=500),
    before: int | None = Query(None, ge=1, description="alleen regels met een lager id (volgende pagina)"),
    actor: str | None = Query(None, max_length=120),
    action: str | None = Query(None, max_length=64),
) -> list[dict]:
    """Newest first. Page further with ``before=<id of the last row>``."""
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if before is not None:
        q = q.where(AuditLog.id < before)
    if actor:
        q = q.where(AuditLog.actor == actor)
    if action:
        q = q.where(AuditLog.action == action)
    return [audit_view(a) for a in (await db.execute(q)).scalars()]
