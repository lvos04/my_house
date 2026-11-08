"""Lights page JSON shape: light groups, lamps and scenes (verbeterlijst 27), plus the button-mapping
step helpers shared by the API (validation, simulation) and the worker (execution)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CommandRecord, Device

ALL_LIGHTS_NAME = "Alle lampen"
# The Hue adapter answers ok/unconfirmed with this prefix when the bridge returned an "errors" list.
BRIDGE_ERROR_PREFIX = "Hue Bridge meldt"
LAST_COMMAND_WINDOW = timedelta(hours=1)
LIGHT_KINDS = ("light_group", "light", "scene")


def command_view(cmd: CommandRecord | None) -> dict | None:
    """A command's outcome for the lights page. A command the bridge answered with errors counts as
    failed, with the bridge's message (never as "sent" or "confirmed")."""
    if cmd is None:
        return None
    status = cmd.status
    if status == "sent" and (cmd.detail or "").startswith(BRIDGE_ERROR_PREFIX):
        status = "failed"
    return {"id": cmd.id, "action": cmd.action, "status": status, "detail": cmd.detail or "", "at": cmd.updated_at}


def is_all_lights(d: Device) -> bool:
    s = d.state or {}
    return bool(s.get("all_lights")) or s.get("owner_type") == "bridge_home"


def group_view(
    d: Device, scene_ids: dict[str, uuid.UUID] | None = None, last_command: CommandRecord | None = None
) -> dict:
    """``LightGroup`` (docs/api.md) for one light_group device; also the ``lights.updated`` payload."""
    s = d.state or {}
    total = int(s.get("lights_total") or 0)
    reach = s.get("lights_reachable")
    all_lights = is_all_lights(d)
    reachable = bool(d.online) and (total == 0 or reach is None or int(reach) > 0)
    name = d.name
    if all_lights and name in ("Lichtgroep", "Hue-apparaat", ""):
        name = ALL_LIGHTS_NAME
    scene_rid = s.get("scene_id")
    return {
        "id": d.id,
        "name": name,
        "on": bool(s.get("on")),
        "brightness": s.get("brightness"),
        "scene": s.get("scene"),
        "scene_id": (scene_ids or {}).get(scene_rid) if scene_rid else None,
        "reachable": reachable,
        "lights_on": int(s.get("lights_on") or 0),
        "lights_total": total,
        "lights_reachable": int(reach) if reach is not None else None,
        "owner_type": s.get("owner_type"),
        "all_lights": all_lights,
        "hidden": all_lights,  # the "all lights" group: shown last, or hidden by the client
        "last_command": command_view(last_command),
    }


def _group_sort_key(g: dict) -> tuple:
    return (1 if g["all_lights"] else 0, g["name"].lower())


async def scene_id_map(db: AsyncSession, home_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Hue scene resource id -> our scene device id."""
    rows = await db.execute(
        select(Device.stable_identity, Device.id).where(Device.home_id == home_id, Device.kind == "scene")
    )
    return {r.stable_identity: r.id for r in rows}


async def last_commands(db: AsyncSession, device_ids: list[uuid.UUID]) -> dict[uuid.UUID, CommandRecord]:
    """Newest command (last hour) per device."""
    if not device_ids:
        return {}
    since = datetime.now(UTC) - LAST_COMMAND_WINDOW
    rows = (
        await db.execute(
            select(CommandRecord)
            .where(CommandRecord.device_id.in_(device_ids), CommandRecord.created_at >= since)
            .order_by(CommandRecord.created_at.desc())
            .limit(500)
        )
    ).scalars()
    out: dict[uuid.UUID, CommandRecord] = {}
    for c in rows:
        out.setdefault(c.device_id, c)
    return out


async def group_view_for(db: AsyncSession, d: Device) -> dict:
    """``group_view`` with the scene id map and the last command (for realtime events)."""
    scenes = await scene_id_map(db, d.home_id) if (d.state or {}).get("scene_id") else {}
    scene_devs = [
        r.id
        for r in (
            await db.execute(
                select(Device.id).where(
                    Device.home_id == d.home_id,
                    Device.kind == "scene",
                    Device.state["group_device_id"].astext == str(d.id),
                )
            )
        )
    ]
    cmds = await last_commands(db, [d.id, *scene_devs])
    newest = max(cmds.values(), key=lambda c: c.created_at, default=None)
    return group_view(d, scenes, newest)


async def lights_view(db: AsyncSession, home_id: uuid.UUID) -> dict:
    devs = list(
        (
            await db.execute(
                select(Device).where(
                    Device.home_id == home_id,
                    Device.kind.in_(LIGHT_KINDS),
                    Device.lifecycle != "archived",
                )
            )
        ).scalars()
    )
    scene_ids = {d.stable_identity: d.id for d in devs if d.kind == "scene"}
    cmds = await last_commands(db, [d.id for d in devs])
    groups_by_id: dict[str, Device] = {str(d.id): d for d in devs if d.kind == "light_group"}
    group_cmd: dict[str, CommandRecord] = {}
    for d in devs:
        c = cmds.get(d.id)
        if c is None:
            continue
        gid = str(d.id) if d.kind == "light_group" else (d.state or {}).get("group_device_id")
        if gid and (gid not in group_cmd or c.created_at > group_cmd[gid].created_at):
            group_cmd[gid] = c
    groups, scenes, lamps = [], [], []
    for d in devs:
        s = d.state or {}
        if d.kind == "light_group":
            groups.append(group_view(d, scene_ids, group_cmd.get(str(d.id))))
        elif d.kind == "scene":
            gid = s.get("group_device_id")
            group = groups_by_id.get(gid) if gid else None
            active = bool(group is not None and (group.state or {}).get("scene_id") == d.stable_identity)
            scenes.append({"id": d.id, "name": d.name, "group_id": gid, "active": active})
        else:
            lamps.append(
                {
                    "id": d.id,
                    "name": d.name,
                    "on": bool(s.get("on")),
                    "brightness": s.get("brightness"),
                    "reachable": bool(d.online) and s.get("reachable") is not False,
                    "model": d.model,
                    "last_command": command_view(cmds.get(d.id)),
                }
            )
    groups.sort(key=_group_sort_key)
    scenes.sort(key=lambda x: x["name"].lower())
    lamps.sort(key=lambda x: x["name"].lower())
    return {"groups": groups, "scenes": scenes, "lights": lamps}


# --- button mapping steps ----------------------------------------------------------------------

LIGHT_ACTIONS = {
    "light_group": ("set_group", "toggle"),
    "light": ("set_light", "toggle"),
    "scene": ("recall_scene",),
}
ZONE_FIELDS = ("mode", "target_c", "fan", "override_minutes")


def mapping_steps(action: dict | None) -> list[dict]:
    """The steps of a mapping's action, each with a "type": "light" (device_id + action) or "zone"
    (zone_id + mode/target_c/fan/override_minutes, or action "resume"). A plain action without
    "steps" is one step (older mappings)."""
    action = action or {}
    raw = action.get("steps") if isinstance(action.get("steps"), list) else [action]
    out = []
    for s in raw:
        if not isinstance(s, dict) or not s:
            continue
        t = s.get("type") or ("zone" if s.get("zone_id") else "light")
        out.append({**s, "type": "zone" if t in ("zone", "climate") else "light"})
    return out


def light_command(target: Device, step: dict) -> tuple[str, dict]:
    """(action, params) to queue for a light step. "toggle" becomes an explicit on/off from the
    current state, so the command itself is never a power toggle."""
    action = step.get("action") or {"light_group": "set_group", "light": "set_light", "scene": "recall_scene"}.get(
        target.kind, ""
    )
    params = dict(step.get("params") or {})
    if action == "toggle":
        on = not bool((target.state or {}).get("on"))
        return ("set_group" if target.kind == "light_group" else "set_light"), {"on": on}
    if action == "recall_scene":
        return action, {}
    return action, {k: v for k, v in params.items() if k in ("on", "brightness")}
