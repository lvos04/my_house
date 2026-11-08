from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from ..config import get_settings
from ..deps import DB, Admin, Auth, get_home
from ..errors import api_error, not_found
from ..models import Device, DeviceBinding, Integration, LogicalRole, Room
from ..security import SecretBox
from ..store import audit, roles_of

router = APIRouter(tags=["devices"])


def providers() -> dict:
    from shc_integrations.registry import PROVIDERS

    return PROVIDERS


async def device_view(db: DB, d: Device) -> dict:
    roles, protected = await roles_of(db, d.id)
    room = await db.get(Room, d.room_id) if d.room_id else None
    return {
        "id": d.id,
        "provider": d.provider,
        "kind": d.kind,
        "name": d.name,
        "stable_identity": d.stable_identity,
        "model": d.model,
        "firmware": d.firmware,
        "room": room.name if room else None,
        "roles": sorted(roles),
        "online": d.online,
        "last_seen": d.last_seen,
        "capabilities": d.capabilities,
        "protected": bool(protected) or "boiler" in roles,
        "compatibility": d.compatibility,
        "lifecycle": d.lifecycle,
    }


@router.get("/homes/{home_id}/devices")
async def devices(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    await get_home(db, home_id)
    rows = (
        await db.execute(select(Device).where(Device.home_id == home_id).order_by(Device.provider, Device.name))
    ).scalars()
    return [await device_view(db, d) for d in rows]


class DevicePatch(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    room_id: uuid.UUID | None = None


@router.patch("/devices/{device_id}")
async def patch_device(device_id: uuid.UUID, body: DevicePatch, db: DB, p: Admin) -> dict:
    d = await db.get(Device, device_id)
    if not d:
        raise not_found("Apparaat")
    for f in body.model_fields_set:
        setattr(d, f, getattr(body, f))
    await db.commit()
    return await device_view(db, d)


class BindIn(BaseModel):
    role: str = Field(max_length=64)


@router.post("/devices/{device_id}/bind")
async def bind(device_id: uuid.UUID, body: BindIn, db: DB, p: Admin) -> dict:
    """Bind a device to a logical role. The previous device keeps its history (binding closed, not deleted)."""
    d = await db.get(Device, device_id)
    if not d:
        raise not_found("Apparaat")
    role = (
        await db.execute(select(LogicalRole).where(LogicalRole.home_id == d.home_id, LogicalRole.key == body.role))
    ).scalar_one_or_none()
    if not role:
        raise not_found("Rol")
    now = datetime.now(UTC)
    for b in (
        await db.execute(
            select(DeviceBinding).where(DeviceBinding.role_id == role.id, DeviceBinding.effective_to.is_(None))
        )
    ).scalars():
        if b.device_id == d.id:
            return await device_view(db, d)
        b.effective_to = now
        old = await db.get(Device, b.device_id)
        if old and old.id != d.id:
            old.lifecycle = "replaced"
    db.add(DeviceBinding(role_id=role.id, device_id=d.id, effective_from=now))
    await audit(db, p.actor, "role_bound", body.role, device=str(d.id))
    await db.commit()
    return await device_view(db, d)


@router.get("/integrations")
async def integrations(db: DB, p: Auth) -> list[dict]:
    rows = (await db.execute(select(Integration))).scalars().all()
    configured = {i.provider for i in rows}
    out = [
        {
            "id": i.id,
            "provider": i.provider,
            "label": i.name,
            "state": i.state,
            "detail": i.detail,
            "last_ok_at": i.last_ok_at,
        }
        for i in rows
    ]
    for name, info in providers().items():
        if name not in configured:
            out.append(
                {
                    "id": None,
                    "provider": name,
                    "label": info.label,
                    "state": "not_configured",
                    "detail": info.needs_manufacturer_app or "",
                    "last_ok_at": None,
                }
            )
    return out


@router.get("/integrations/{provider}/instructions")
async def instructions(provider: str, p: Auth) -> dict:
    info = providers().get(provider)
    if not info:
        raise not_found("Integratie")
    return {
        "provider": provider,
        "label": info.label,
        "steps": info.instructions.steps,
        "needs_manufacturer_app": info.needs_manufacturer_app,
        "works_locally": info.works_locally,
    }


class DiscoverIn(BaseModel):
    home_id: uuid.UUID
    timeout_s: float = Field(default=5, ge=1, le=30)


def _disc_view(x: Any) -> dict:
    return {
        "provider": x.provider,
        "stable_identity": x.stable_identity,
        "kind": x.kind,
        "name": x.name,
        "host": x.host,
        "model": x.model,
        "firmware": x.firmware,
    }


@router.post("/integrations/{provider}/discover")
async def discover(provider: str, body: DiscoverIn, p: Admin) -> list[dict]:
    info = providers().get(provider)
    if not info:
        raise not_found("Integratie")
    adapter = info.adapter({}, {})
    try:
        found = await adapter.discover(body.timeout_s)
    except Exception as exc:
        raise api_error(502, "discovery_failed", f"Zoeken mislukt: {exc}. Voer het IP-adres handmatig in.") from exc
    finally:
        await adapter.close()
    return [_disc_view(x) for x in found]


class PairIn(BaseModel):
    home_id: uuid.UUID
    host: str = Field(max_length=255)
    name: str | None = Field(default=None, max_length=100)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip()
        try:
            ip = ipaddress.ip_address(v)
            if not (ip.is_private or ip.is_loopback):
                raise ValueError("Alleen apparaten in het lokale netwerk zijn toegestaan.")
        except ValueError as exc:
            if "lokale" in str(exc):
                raise
            if not v.replace("-", "").replace(".", "").isalnum():
                raise ValueError("Ongeldig adres") from None
        return v


@router.post("/integrations/{provider}/pair")
async def pair(provider: str, body: PairIn, db: DB, p: Admin):
    from shc_integrations.base import PairingInstructions

    info = providers().get(provider)
    if not info:
        raise not_found("Integratie")
    home = await get_home(db, body.home_id)
    adapter = info.adapter({"host": body.host, **body.options}, {})
    try:
        result = await adapter.pair(body.host, **body.options)
    except Exception as exc:
        raise api_error(502, getattr(exc, "code", "pair_failed"), f"Koppelen mislukt: {exc}") from exc
    finally:
        await adapter.close()
    if isinstance(result, PairingInstructions):
        return JSONResponse(
            status_code=202,
            content={
                "status": "action_required",
                "steps": result.steps,
                "retry_after_s": result.retry_after_s,
            },
        )
    box = SecretBox(get_settings().resolved_secret_key())
    dev = result.device
    # The adapter fills in what it learned (serial, product type, API version, MAC, key version).
    config = {
        "host": body.host,
        **body.options,
        **getattr(adapter, "config", {}),
        **(dev.extra.get("config", {}) if dev.extra else {}),
    }
    integ = Integration(
        home_id=home.id,
        provider=provider,
        name=body.name or dev.name,
        config=config,
        credentials_enc=box.encrypt(result.credentials) if result.credentials else None,
        state="warn",
        detail="Gekoppeld; wacht op eerste meting.",
    )
    db.add(integ)
    await db.flush()
    existing = (
        await db.execute(
            select(Device).where(
                Device.home_id == home.id,
                Device.provider == provider,
                Device.stable_identity == result.stable_identity,
            )
        )
    ).scalar_one_or_none()
    d = existing or Device(
        home_id=home.id,
        provider=provider,
        kind=dev.kind,
        name=body.name or dev.name,
        stable_identity=result.stable_identity,
    )
    d.integration_id, d.model, d.firmware, d.lifecycle = integ.id, dev.model, dev.firmware, "active"
    if not existing:
        db.add(d)
    await audit(db, p.actor, "integration_paired", provider, host=body.host)
    await db.commit()
    return await device_view(db, d)


@router.delete("/integrations/{integration_id}", status_code=204)
async def remove_integration(integration_id: uuid.UUID, db: DB, p: Admin):
    i = await db.get(Integration, integration_id)
    if not i:
        raise not_found("Integratie")
    i.enabled = False
    i.credentials_enc = None
    i.state, i.detail = "not_configured", "Ontkoppeld"
    await audit(db, p.actor, "integration_removed", i.provider)
    await db.commit()
    return JSONResponse(status_code=204, content=None)


class ZigbeeSetup(BaseModel):
    home_id: uuid.UUID


@router.post("/integrations/zigbee/setup")
async def zigbee_setup(body: ZigbeeSetup, db: DB, p: Admin) -> dict:
    """Register the internal Zigbee2MQTT bridge (no network call: the worker connects to MQTT)."""
    home = await get_home(db, body.home_id)
    integ = (
        await db.execute(select(Integration).where(Integration.home_id == home.id, Integration.provider == "zigbee"))
    ).scalar_one_or_none()
    if integ is None:
        s = get_settings()
        integ = Integration(
            home_id=home.id,
            provider="zigbee",
            name="Zigbee",
            config={"host": s.mqtt_host, "port": s.mqtt_port, "username": s.mqtt_username or "shc"},
            state="warn",
            detail="Wacht op verbinding met Zigbee2MQTT.",
        )
        db.add(integ)
        await db.flush()
    integ.enabled = True
    bridge = (
        await db.execute(select(Device).where(Device.home_id == home.id, Device.kind == "zigbee_bridge"))
    ).scalar_one_or_none()
    if bridge is None:
        bridge = Device(
            home_id=home.id,
            integration_id=integ.id,
            provider="zigbee",
            kind="zigbee_bridge",
            name="Zigbee-coördinator",
            stable_identity="zigbee2mqtt-bridge",
            compatibility="untested",
        )
        db.add(bridge)
    await audit(db, p.actor, "zigbee_setup")
    await db.commit()
    return await device_view(db, bridge)


class PermitJoin(BaseModel):
    home_id: uuid.UUID
    seconds: int = Field(default=120, ge=30, le=254)


@router.post("/integrations/zigbee/permit-join", status_code=202)
async def zigbee_permit_join(body: PermitJoin, db: DB, p: Admin):
    from ..commanding import queue_command

    bridge = (
        await db.execute(select(Device).where(Device.home_id == body.home_id, Device.kind == "zigbee_bridge"))
    ).scalar_one_or_none()
    if bridge is None:
        raise api_error(409, "not_configured", "Zigbee is nog niet ingesteld.")
    cmd = await queue_command(db, body.home_id, bridge, "permit_join", {"seconds": body.seconds}, "user", p.actor)
    await db.commit()
    until = datetime.now(UTC).timestamp() + body.seconds
    return JSONResponse(
        status_code=202,
        content={"command_id": str(cmd.id), "open_until": datetime.fromtimestamp(until, UTC).isoformat()},
    )
