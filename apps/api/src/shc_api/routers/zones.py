from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .. import events, views
from ..commanding import CommandRejected, request_zone_command
from ..deps import DB, Admin, Auth, Control, get_home
from ..errors import api_error, not_found
from ..models import ClimateZone, Home, ScheduleBlock, SensorReading, ZoneLog
from ..store import audit

router = APIRouter(tags=["climate"])

Mode = Literal["off", "heat", "cool", "auto", "dry", "fan"]


async def _zone(db: DB, zone_id: uuid.UUID) -> tuple[ClimateZone, Home]:
    z = await db.get(ClimateZone, zone_id)
    if not z:
        raise not_found("Zone")
    return z, await get_home(db, z.home_id)


@router.get("/homes/{home_id}/zones")
async def list_zones(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    home = await get_home(db, home_id)
    zs = (
        await db.execute(select(ClimateZone).where(ClimateZone.home_id == home_id).order_by(ClimateZone.name.desc()))
    ).scalars()
    return [await views.zone_view(db, z, home) for z in zs]


@router.get("/zones/{zone_id}")
async def get_zone(zone_id: uuid.UUID, db: DB, p: Auth) -> dict:
    z, h = await _zone(db, zone_id)
    return await views.zone_view(db, z, h)


class ZoneCommand(BaseModel):
    mode: Mode | None = None
    target_c: float | None = Field(default=None, ge=5, le=35)
    fan: Literal["auto", "low", "medium", "high"] | None = None
    override_minutes: int | None = Field(default=None, ge=5, le=24 * 60)


@router.post("/zones/{zone_id}/command", status_code=202)
async def command(zone_id: uuid.UUID, body: ZoneCommand, db: DB, p: Control):
    z, home = await _zone(db, zone_id)
    try:
        cmd = await request_zone_command(
            db,
            z,
            home,
            mode=body.mode,
            target_c=body.target_c,
            fan=body.fan,
            override_minutes=body.override_minutes,
            source="user",
            actor=p.actor,
            log_name=p.user.username,
        )
    except CommandRejected as e:
        raise api_error(e.status, e.code, e.message) from e
    await db.commit()
    return JSONResponse({"command_id": str(cmd.id), "status": "queued"}, status_code=202)


@router.post("/zones/{zone_id}/resume")
async def resume(zone_id: uuid.UUID, db: DB, p: Control) -> dict:
    z, home = await _zone(db, zone_id)
    z.control, z.override_until, z.override_source = "schedule", None, None
    z.last_commanded = {}  # let the engine re-evaluate from the unit's current state
    db.add(ZoneLog(zone_id=z.id, kind="user", message=f"{p.user.username}: terug naar schema"))
    view = await views.zone_view(db, z, home)
    await events.publish(db, "zone.updated", home.id, view)
    await db.commit()
    return view


@router.get("/zones/{zone_id}/history")
async def history(zone_id: uuid.UUID, db: DB, p: Auth, hours: int = Query(24, ge=1, le=24 * 31)) -> dict:
    z, _ = await _zone(db, zone_id)
    since = datetime.now(UTC) - timedelta(hours=hours)
    sensor = z.sensor_device_id or z.unit_device_id
    out: dict[str, list] = {"temperature": [], "target": []}
    for key, dev, metric in (
        ("temperature", sensor, "temperature"),
        ("target", z.unit_device_id, "target_c"),
    ):
        if dev is None:
            continue
        rows = (
            await db.execute(
                select(SensorReading.observed_at, SensorReading.value)
                .where(
                    SensorReading.device_id == dev,
                    SensorReading.metric == metric,
                    SensorReading.observed_at >= since,
                )
                .order_by(SensorReading.observed_at)
            )
        ).all()
        out[key] = [[r.observed_at, r.value] for r in rows]
    return out


@router.get("/zones/{zone_id}/log")
async def zone_log(zone_id: uuid.UUID, db: DB, p: Auth, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    rows = (
        await db.execute(select(ZoneLog).where(ZoneLog.zone_id == zone_id).order_by(ZoneLog.at.desc()).limit(limit))
    ).scalars()
    return [{"at": r.at, "kind": r.kind, "message": r.message} for r in rows]


class Block(BaseModel):
    days: list[int] = Field(min_length=1, max_length=7)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    target_c: float = Field(ge=10, le=30)
    mode: Mode | None = None
    label: str = Field(default="", max_length=50)


class Schedule(BaseModel):
    blocks: list[Block] = Field(max_length=100)


@router.get("/zones/{zone_id}/schedule")
async def get_schedule(zone_id: uuid.UUID, db: DB, p: Auth) -> dict:
    rows = (await db.execute(select(ScheduleBlock).where(ScheduleBlock.zone_id == zone_id))).scalars()
    return {
        "blocks": [
            {"days": b.days, "start": b.start, "target_c": b.target_c, "mode": b.mode, "label": b.label}
            for b in sorted(rows, key=lambda b: b.start)
        ]
    }


@router.put("/zones/{zone_id}/schedule")
async def put_schedule(zone_id: uuid.UUID, body: Schedule, db: DB, p: Admin) -> dict:
    z, _ = await _zone(db, zone_id)
    for b in body.blocks:
        if any(d < 0 or d > 6 for d in b.days):
            raise api_error(422, "validation_error", "Dagen moeten 0 (maandag) t/m 6 (zondag) zijn.")
    await db.execute(delete(ScheduleBlock).where(ScheduleBlock.zone_id == zone_id))
    for b in body.blocks:
        db.add(
            ScheduleBlock(
                zone_id=zone_id,
                days=sorted(set(b.days)),
                start=b.start,
                target_c=b.target_c,
                mode=b.mode,
                label=b.label,
            )
        )
    db.add(ZoneLog(zone_id=z.id, kind="config", message=f"{p.user.username}: schema aangepast"))
    await audit(db, p.actor, "schedule_updated", str(zone_id))
    await db.commit()
    return await get_schedule(zone_id, db, p)


class PolicyIn(BaseModel):
    band_c: float = Field(ge=0.2, le=3)
    min_interval_s: int = Field(ge=300, le=7200)
    max_step_c: float = Field(ge=0.5, le=3)
    max_starts_per_hour: int = Field(ge=1, le=6)
    stale_after_s: int = Field(ge=300, le=7200)


@router.put("/zones/{zone_id}/policy")
async def put_policy(zone_id: uuid.UUID, body: PolicyIn, db: DB, p: Admin) -> dict:
    z, h = await _zone(db, zone_id)
    z.policy = {**(z.policy or {}), **body.model_dump()}
    db.add(ZoneLog(zone_id=z.id, kind="config", message=f"{p.user.username}: regelgrenzen aangepast"))
    await db.commit()
    return await views.zone_view(db, z, h)


class ZoneConfig(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    unit_device_id: uuid.UUID | None = None
    sensor_device_id: uuid.UUID | None = None
    outdoor_group: str | None = Field(default=None, max_length=64)


@router.patch("/zones/{zone_id}")
async def patch_zone(zone_id: uuid.UUID, body: ZoneConfig, db: DB, p: Admin) -> dict:
    z, h = await _zone(db, zone_id)
    for f in body.model_fields_set:
        setattr(z, f, getattr(body, f))
    db.add(ZoneLog(zone_id=z.id, kind="config", message=f"{p.user.username}: zone-koppeling aangepast"))
    await db.commit()
    return await views.zone_view(db, z, h)
