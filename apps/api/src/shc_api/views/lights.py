"""Lights page JSON shape: light groups and scenes."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Device


async def lights_view(db: AsyncSession, home_id: uuid.UUID) -> dict:
    devs = (
        await db.execute(select(Device).where(Device.home_id == home_id, Device.kind.in_(["light_group", "scene"])))
    ).scalars()
    groups, scenes = [], []
    for d in devs:
        s = d.state or {}
        if d.kind == "light_group":
            groups.append(
                {
                    "id": d.id,
                    "name": d.name,
                    "on": bool(s.get("on")),
                    "brightness": s.get("brightness"),
                    "scene": s.get("scene"),
                    "reachable": d.online,
                    "lights_on": s.get("lights_on", 0),
                    "lights_total": s.get("lights_total", 0),
                }
            )
        else:
            scenes.append({"id": d.id, "name": d.name, "group_id": s.get("group_device_id")})
    groups.sort(key=lambda g: g["name"])
    return {"groups": groups, "scenes": scenes}
