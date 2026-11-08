"""Phone pairing via QR code (Android app)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from ..config import get_settings
from ..deps import DB, Admin
from ..errors import api_error, not_found
from ..models import ApiClient, Home, PairingCode, User
from ..security import new_pairing_code, new_token, normalize_pairing_code, token_hash
from ..store import audit, get_setting
from .auth import user_view

router = APIRouter(tags=["pairing"])
CODE_TTL = timedelta(minutes=10)


class CodeRequest(BaseModel):
    label: str | None = Field(default=None, max_length=100)


async def server_urls(db: DB, request: Request | None = None) -> tuple[str | None, str | None]:
    s = get_settings()
    lan = await get_setting(db, "lan_url", s.lan_url)
    remote = await get_setting(db, "remote_url", s.remote_url)
    if not lan and request is not None:
        lan = str(request.base_url).rstrip("/")
    return lan, remote


@router.post("/pairing/codes")
async def create_code(body: CodeRequest, request: Request, db: DB, p: Admin) -> dict:
    code = new_pairing_code()
    expires = datetime.now(UTC) + CODE_TTL
    db.add(PairingCode(code_hash=token_hash(code), user_id=p.user.id, label=body.label, expires_at=expires))
    home = (await db.execute(select(Home).where(Home.active_to.is_(None)).order_by(Home.active_from))).scalars().first()
    lan, remote = await server_urls(db, request)
    await audit(db, p.actor, "pairing_code_created", body.label)
    await db.commit()
    payload = {
        "v": 1,
        "type": "shc-pair",
        "code": code,
        "lan": lan,
        "remote": remote,
        "home": home.name if home else None,
    }
    return {"code": code, "expires_at": expires, "qr_payload": json.dumps(payload, separators=(",", ":"))}


class Redeem(BaseModel):
    code: str = Field(min_length=8, max_length=12)
    device_name: str = Field(min_length=1, max_length=100)


@router.post("/pairing/redeem")
async def redeem(body: Redeem, request: Request, db: DB) -> dict:
    code = normalize_pairing_code(body.code)
    now = datetime.now(UTC)
    # Atomic single use: mark used only if still unused and valid.
    res = await db.execute(
        update(PairingCode)
        .where(
            PairingCode.code_hash == token_hash(code),
            PairingCode.used_at.is_(None),
            PairingCode.expires_at > now,
        )
        .values(used_at=now)
        .returning(PairingCode.user_id)
    )
    row = res.first()
    if row is None:
        await db.rollback()
        raise api_error(400, "invalid_code", "Code ongeldig of verlopen. Maak een nieuwe QR-code in de web-app.")
    user = await db.get(User, row.user_id)
    assert user is not None
    token = new_token()
    client = ApiClient(user_id=user.id, name=body.device_name, token_hash=token_hash(token))
    db.add(client)
    await audit(db, user.username, "phone_paired", body.device_name)
    home = (await db.execute(select(Home).where(Home.active_to.is_(None)).order_by(Home.active_from))).scalars().first()
    lan, remote = await server_urls(db, request)
    await db.commit()
    return {
        "token": token,
        "user": user_view(user),
        "server": {
            "lan_url": lan,
            "remote_url": remote,
            "home_id": home.id if home else None,
            "home_name": home.name if home else None,
        },
    }


@router.get("/clients")
async def clients(db: DB, p: Admin) -> list[dict]:
    rows = (await db.execute(select(ApiClient).order_by(ApiClient.created_at.desc()))).scalars()
    return [
        {
            "id": c.id,
            "name": c.name,
            "created_at": c.created_at,
            "last_used_at": c.last_used_at,
            "revoked": c.revoked_at is not None,
        }
        for c in rows
    ]


@router.delete("/clients/{client_id}", status_code=204)
async def revoke(client_id: uuid.UUID, db: DB, p: Admin) -> Response:
    c = await db.get(ApiClient, client_id)
    if not c:
        raise not_found("Telefoon")
    c.revoked_at = datetime.now(UTC)
    await audit(db, p.actor, "phone_revoked", c.name)
    await db.commit()
    return Response(status_code=204)
