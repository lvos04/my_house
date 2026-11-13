"""Phone pairing via QR code (Android app), phone tokens and the server identity.

Verbeterlijst 17/18: the home address must be configured (never guessed from the incoming request),
http is only allowed on the home network, the remote address must be https; codes carry a role
(control by default) and tokens expire after 180 days unless the app refreshes them."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from ..config import get_settings
from ..deps import CLIENT_TTL, DB, Phone, WebAdmin
from ..errors import SECRET_KEY_INVALID_RESPONSE, api_error, not_found, require_secret_box
from ..events import publish_auth_revoked
from ..models import ApiClient, Home, PairingCode, Setting, User
from ..security import (
    AttemptLimiter,
    lan_url_problem,
    new_pairing_code,
    new_token,
    normalize_pairing_code,
    remote_url_problem,
    token_hash,
)
from ..store import audit, get_setting
from .auth import client_ip, password_matches, too_many, user_view

router = APIRouter(tags=["pairing"])
CODE_TTL = timedelta(minutes=10)
# Failed redeem attempts per client IP (codes are 8 characters, valid 10 minutes).
_redeem_attempts = AttemptLimiter(10, 300, max_keys=10_000)


async def configured_urls(db: DB) -> tuple[str | None, str | None]:
    """Addresses handed to phones: the value set in Systeem, else SHC_LAN_URL / SHC_REMOTE_URL.

    Never derived from the request: via the tunnel that would hand out http://<tunnel-host> as the
    home address (token over the internet in plain text), and at home it drops the port."""
    s = get_settings()
    lan = await get_setting(db, "lan_url") or s.lan_url or None
    remote = await get_setting(db, "remote_url") or s.remote_url or None
    return lan, remote


async def server_identity(db: DB) -> str:
    """Stable random id of this installation (Setting "server_id"), created on first use.

    Phones store it at pairing and check GET /pairing/identity before sending their token to an
    address, so they never hand the token to another device that answers on the home address."""
    sid = await get_setting(db, "server_id")
    if sid:
        return str(sid)
    await db.execute(insert(Setting).values(key="server_id", value={"v": str(uuid.uuid4())}).on_conflict_do_nothing())
    return str(await get_setting(db, "server_id"))


async def _active_home(db: DB) -> Home | None:
    return (await db.execute(select(Home).where(Home.active_to.is_(None)).order_by(Home.active_from))).scalars().first()


IDENTITY_PROOF_ALG = "hmac-sha256-v1"


def identity_proof(token_hash_hex: str, server_id: str, nonce: str) -> str:
    """Answer to the phone's challenge (verbeterlijst 17).

    ``HMAC-SHA256(key, msg)`` as lowercase hex, where key = the UTF-8 bytes of sha256(token) in
    lowercase hex (what the server stores) and msg = the UTF-8 bytes of the three lines
    "shc-identity-v1", server_id and nonce joined by a single LF. Only this server (which stores the
    token hash) and the phone (which holds the token) can compute it; a device that merely read the
    public server_id cannot."""
    msg = f"shc-identity-v1\n{server_id}\n{nonce}"
    return hmac.new(token_hash_hex.encode(), msg.encode(), hashlib.sha256).hexdigest()


@router.get("/pairing/identity")
async def identity(
    db: DB,
    nonce: str | None = Query(None, min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
    client_id: uuid.UUID | None = None,
) -> dict:
    """No login needed: lets a phone check it is talking to the server it was paired with.

    ``server_id`` alone is public (it is in the QR code and anyone on the network can read it), so a
    paired phone also sends a fresh random ``nonce`` and its ``client_id``; the answer then carries
    ``proof`` = :func:`identity_proof`. The phone sends its token only when the proof matches. For an
    unknown client id ``proof`` is null. Revoked or expired phones still get a proof, so the app can
    show "koppel opnieuw" after the 401 instead of "onbekende server"."""
    sid = await server_identity(db)
    home = await _active_home(db)
    out: dict = {"server_id": sid, "name": home.name if home else None}
    if nonce is not None and client_id is not None:
        hashed = (await db.execute(select(ApiClient.token_hash).where(ApiClient.id == client_id))).scalar()
        out["proof"] = identity_proof(hashed, sid, nonce) if hashed else None
        out["proof_alg"] = IDENTITY_PROOF_ALG
    await db.commit()
    return out


class CodeRequest(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    role: Literal["control", "viewer", "admin"] = "control"
    # Required for role "admin": the creator re-enters their own password.
    password: str | None = Field(default=None, max_length=256)


@router.post("/pairing/codes", responses=SECRET_KEY_INVALID_RESPONSE)
async def create_code(body: CodeRequest, request: Request, db: DB, p: WebAdmin) -> dict:
    lan, remote = await configured_urls(db)
    if not lan:
        raise api_error(
            409,
            "lan_url_required",
            "Stel eerst het thuisadres van de server in via Systeem › Adressen (bijvoorbeeld "
            "http://192.168.1.20:8088). De telefoon gebruikt dat adres thuis.",
        )
    problem = lan_url_problem(lan)
    if problem:
        raise api_error(409, "lan_url_invalid", f"{problem} Pas het aan via Systeem › Adressen.")
    if remote and (problem := remote_url_problem(remote)):
        raise api_error(409, "remote_url_invalid", f"{problem} Pas het aan via Systeem › Adressen.")
    if body.role == "admin":
        if not body.password:
            raise api_error(
                403,
                "password_required",
                "Voer je wachtwoord in om een telefoon met beheerrechten te koppelen.",
            )
        user = await db.get(User, p.user.id)
        assert user is not None
        if not await password_matches(db, user, body.password, ip=client_ip(request)):
            await db.commit()
            raise api_error(403, "wrong_password", "Je wachtwoord klopt niet.")
    box = require_secret_box()  # KLEIN-45: 503 secret_key_invalid zonder bruikbare sleutel
    code = new_pairing_code()
    expires = datetime.now(UTC) + CODE_TTL
    pc = PairingCode(
        code_hash=box.pairing_code_hash(code), user_id=p.user.id, label=body.label, expires_at=expires, role=body.role
    )
    db.add(pc)
    await db.flush()
    home = await _active_home(db)
    sid = await server_identity(db)
    await audit(db, p.actor, "pairing_code_created", body.label, role=body.role)
    await db.commit()
    payload = {
        "v": 1,
        "type": "shc-pair",
        "code": code,
        "lan": lan,
        "remote": remote,
        "home": home.name if home else None,
        "server_id": sid,
        "role": body.role,
    }
    return {
        "code": code,
        "expires_at": expires,
        "role": body.role,
        "server_id": sid,
        "qr_payload": json.dumps(payload, separators=(",", ":")),
    }


class Redeem(BaseModel):
    code: str = Field(min_length=8, max_length=12)
    device_name: str = Field(min_length=1, max_length=100)


def client_view(c: ApiClient, username: str | None = None) -> dict:
    now = datetime.now(UTC)
    v = {
        "id": c.id,
        "name": c.name,
        "role": c.role,
        "created_at": c.created_at,
        "last_used_at": c.last_used_at,
        "expires_at": c.expires_at,
        "refreshed_at": c.refreshed_at,
        "revoked": c.revoked_at is not None,
        "expired": bool(c.expires_at and c.expires_at <= now),
    }
    if username is not None:
        v["user"] = username
    return v


@router.post("/pairing/redeem", responses=SECRET_KEY_INVALID_RESPONSE)
async def redeem(body: Redeem, request: Request, db: DB) -> dict:
    ip = client_ip(request)
    if _redeem_attempts.blocked(ip):
        raise too_many(_redeem_attempts.retry_after(ip))
    code = normalize_pairing_code(body.code)
    # KLEIN-45: zelfde HMAC als bij aanmaken; een code die nog als kale SHA-256 is opgeslagen wordt niet gevonden.
    code_hash = require_secret_box().pairing_code_hash(code)
    now = datetime.now(UTC)
    # Atomic single use: mark used only if still unused and valid.
    res = await db.execute(
        update(PairingCode)
        .where(
            PairingCode.code_hash == code_hash,
            PairingCode.used_at.is_(None),
            PairingCode.expires_at > now,
        )
        .values(used_at=now)
        .returning(PairingCode.id, PairingCode.user_id, PairingCode.role)
    )
    row = res.first()
    user = await db.get(User, row.user_id) if row else None
    if row is None or user is None or user.disabled:
        await db.rollback()
        _redeem_attempts.hit(ip)
        raise api_error(400, "invalid_code", "Code ongeldig of verlopen. Maak een nieuwe QR-code in de web-app.")
    role = row.role or "control"
    token = new_token()
    client = ApiClient(
        user_id=user.id, name=body.device_name, token_hash=token_hash(token), role=role, expires_at=now + CLIENT_TTL
    )
    db.add(client)
    await db.flush()
    await audit(db, user.username, "phone_paired", body.device_name, role=role, client=str(client.id), ip=ip)
    home = await _active_home(db)
    lan, remote = await configured_urls(db)
    sid = await server_identity(db)
    await db.commit()
    _redeem_attempts.reset(ip)
    return {
        "token": token,
        "user": user_view(user),
        "client": client_view(client),
        "server": {
            "server_id": sid,
            "lan_url": lan,
            "remote_url": remote,
            "home_id": home.id if home else None,
            "home_name": home.name if home else None,
        },
    }


@router.get("/clients")
async def clients(db: DB, p: WebAdmin) -> list[dict]:
    rows = await db.execute(
        select(ApiClient, User.username).join(User, User.id == ApiClient.user_id).order_by(ApiClient.created_at.desc())
    )
    return [client_view(c, username) for c, username in rows]


# --- the calling phone itself (bearer token) ----------------------------------------------------


@router.get("/clients/self")
async def client_self(db: DB, p: Phone) -> dict:
    c = await db.get(ApiClient, p.client_id)
    if not c:
        raise not_found("Telefoon")
    return client_view(c, p.user.username)


@router.post("/clients/self/refresh")
async def refresh_self(db: DB, p: Phone) -> dict:
    """New token with a new expiry date (180 days); the old token stops working immediately, so the
    app must store the new one before using it."""
    token = new_token()
    now = datetime.now(UTC)
    res = await db.execute(
        update(ApiClient)
        .where(ApiClient.id == p.client_id, ApiClient.revoked_at.is_(None))
        .values(token_hash=token_hash(token), expires_at=now + CLIENT_TTL, refreshed_at=now, last_used_at=now)
        .returning(ApiClient.expires_at)
    )
    expires = res.scalar_one_or_none()
    if expires is None:
        raise not_found("Telefoon")
    await audit(db, p.actor, "phone_token_refreshed", str(p.client_id))
    await db.commit()
    return {"token": token, "expires_at": expires}


@router.delete("/clients/self", status_code=204)
async def unpair_self(db: DB, p: Phone) -> Response:
    """ "Ontkoppelen" in the app: the token stops working on the server too."""
    await db.execute(
        update(ApiClient)
        .where(ApiClient.id == p.client_id, ApiClient.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await publish_auth_revoked(db, reason="unpaired", client_ids=[p.client_id])
    await audit(db, p.actor, "phone_unpaired", str(p.client_id))
    await db.commit()
    return Response(status_code=204)


@router.delete("/clients/{client_id}", status_code=204)
async def revoke(client_id: uuid.UUID, db: DB, p: WebAdmin) -> Response:
    c = await db.get(ApiClient, client_id)
    if not c:
        raise not_found("Telefoon")
    if c.revoked_at is None:
        c.revoked_at = datetime.now(UTC)
    await publish_auth_revoked(db, reason="phone_revoked", client_ids=[c.id])
    await audit(db, p.actor, "phone_revoked", c.name, client=str(c.id))
    await db.commit()
    return Response(status_code=204)
