from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .. import views
from ..deps import DB, Admin, Auth, get_home
from ..errors import api_error, not_found
from ..models import ButtonMapping, Device
from ..policy import PolicyDenied
from ..store import audit
from .zones import queue_command

router = APIRouter(tags=["lights"])


@router.get("/homes/{home_id}/lights")
async def lights(home_id: uuid.UUID, db: DB, p: Auth) -> dict:
    await get_home(db, home_id)
    return await views.lights_view(db, home_id)


class GroupCommand(BaseModel):
    on: bool | None = None
    brightness: int | None = Field(default=None, ge=1, le=100)
    scene_id: uuid.UUID | None = None


@router.post("/lights/groups/{group_id}", status_code=202)
async def group_command(group_id: uuid.UUID, body: GroupCommand, db: DB, p: Auth):
    if not p.is_admin:
        raise api_error(403, "forbidden", "Alleen-lezen account.")
    g = await db.get(Device, group_id)
    if not g or g.kind != "light_group":
        raise not_found("Lichtgroep")
    try:
        if body.scene_id:
            scene = await db.get(Device, body.scene_id)
            if not scene or scene.kind != "scene":
                raise not_found("Scène")
            cmd = await queue_command(db, g.home_id, scene, "recall_scene", {}, "user", p.actor)
        else:
            params = {
                k: v for k, v in {"on": body.on, "brightness": body.brightness}.items() if v is not None
            }
            if not params:
                raise api_error(422, "validation_error", "Geen wijziging opgegeven.")
            cmd = await queue_command(db, g.home_id, g, "set_group", params, "user", p.actor)
    except PolicyDenied as e:
        raise api_error(409, "policy_denied", e.message) from e
    await db.commit()
    return JSONResponse({"command_id": str(cmd.id), "status": "queued"}, status_code=202)


# --- buttons ------------------------------------------------------------------


def mapping_conflicts(mappings: list[dict], bridge_rules: set[str]) -> dict[int, str]:
    """index -> conflict message. Pure function, tested."""
    out: dict[int, str] = {}
    by_key: dict[tuple[str, str], list[int]] = {}
    for i, m in enumerate(mappings):
        by_key.setdefault((m["button"], m["event"]), []).append(i)
        if m["mode"] == "combined" and not m.get("combined_confirmed"):
            out[i] = "Gecombineerde actie (licht + airco) moet je bewust bevestigen."
        if m["mode"] in ("lights", "combined") and m["button"] in bridge_rules:
            out[i] = "De Hue Bridge voert voor deze knop zelf al een actie uit; dubbele bediening mogelijk."
    for idxs in by_key.values():
        if len(idxs) > 1:
            for i in idxs:
                out[i] = "Meerdere acties op hetzelfde knopevent."
    return out


@router.get("/homes/{home_id}/buttons")
async def buttons(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    devs = (
        (await db.execute(select(Device).where(Device.home_id == home_id, Device.kind == "button")))
        .scalars()
        .all()
    )
    out = []
    for d in devs:
        maps = (
            (await db.execute(select(ButtonMapping).where(ButtonMapping.device_id == d.id))).scalars().all()
        )
        as_dicts = [
            {
                "id": m.id,
                "button": m.button,
                "event": m.event,
                "mode": m.mode,
                "action": m.action,
                "combined_confirmed": m.combined_confirmed,
            }
            for m in maps
        ]
        conflicts = mapping_conflicts(as_dicts, set((d.state or {}).get("bridge_rules", [])))
        for i, m in enumerate(as_dicts):
            m["conflict"] = conflicts.get(i)
        out.append(
            {
                "id": d.id,
                "name": d.name,
                "model": d.model,
                "events": (d.state or {}).get("events", ["initial_press", "short_release", "long_press"]),
                "mapping": as_dicts,
            }
        )
    return out


class MappingIn(BaseModel):
    button: str = Field(max_length=32)
    event: str = Field(max_length=32)
    mode: Literal["lights", "climate", "combined"]
    action: dict[str, Any]
    combined_confirmed: bool = False


@router.put("/buttons/{device_id}/mappings")
async def put_mappings(device_id: uuid.UUID, body: list[MappingIn], db: DB, p: Admin):
    d = await db.get(Device, device_id)
    if not d or d.kind != "button":
        raise not_found("Knop")
    as_dicts = [m.model_dump() for m in body]
    conflicts = mapping_conflicts(as_dicts, set((d.state or {}).get("bridge_rules", [])))
    blocking = {i: c for i, c in conflicts.items() if "bevestigen" in c or "Meerdere" in c}
    if blocking:
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "conflict",
                    "message": "Knopkoppeling heeft conflicten.",
                    "conflicts": [{"index": i, "message": c} for i, c in blocking.items()],
                }
            },
        )
    await db.execute(delete(ButtonMapping).where(ButtonMapping.device_id == device_id))
    for m in body:
        db.add(ButtonMapping(home_id=d.home_id, device_id=device_id, **m.model_dump()))
    await audit(db, p.actor, "button_mappings", d.name, count=len(body))
    await db.commit()
    return await buttons(d.home_id, db, p)
