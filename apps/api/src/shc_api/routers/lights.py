"""Lights (Hue groups and scenes) and button mappings (verbeterlijst 25, 27).

Button mappings: 64-character button/event ids (real Hue ids are 36), steps that fit the mapping's
mode ("lights": lamp/group/scene steps only, "climate": zone steps only, "combined": both, after an
explicit confirmation), CRUD per mapping, and ``POST /buttons/{id}/simulate`` that shows what a
press would do — including refusals and conflicts — without executing anything. Climate steps go
through ``commanding.request_zone_command``, the same path as ``POST /zones/{id}/command``.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .. import climate as cl
from .. import views
from ..commanding import CommandRejected, queue_command, request_zone_command
from ..deps import DB, Admin, Auth, Control, get_home
from ..errors import api_error, not_found
from ..models import ButtonMapping, ClimateZone, Device, Home
from ..policy import PolicyDenied
from ..store import audit
from ..views.lights import LIGHT_ACTIONS, light_command, mapping_steps

router = APIRouter(tags=["lights"])

ID_MAX = 64
FAN_SPEEDS = ("auto", "low", "medium_low", "medium", "medium_high", "high", "quiet", "turbo")


@router.get("/homes/{home_id}/lights")
async def lights(home_id: uuid.UUID, db: DB, p: Auth) -> dict:
    await get_home(db, home_id)
    return await views.lights_view(db, home_id)


class GroupCommand(BaseModel):
    on: bool | None = None
    brightness: int | None = Field(default=None, ge=1, le=100)
    scene_id: uuid.UUID | None = None


@router.post("/lights/groups/{group_id}", status_code=202)
async def group_command(group_id: uuid.UUID, body: GroupCommand, db: DB, p: Control):
    g = await db.get(Device, group_id)
    if not g or g.kind != "light_group" or g.lifecycle == "archived":
        raise not_found("Lichtgroep")
    try:
        if body.scene_id:
            scene = await db.get(Device, body.scene_id)
            if not scene or scene.kind != "scene" or scene.home_id != g.home_id:
                raise not_found("Scène")
            cmd = await queue_command(db, g.home_id, scene, "recall_scene", {}, "user", p.actor)
            await audit(
                db, p.actor, "light_command", g.name, device=str(g.id), command="recall_scene", scene=scene.name
            )
        else:
            params = {k: v for k, v in {"on": body.on, "brightness": body.brightness}.items() if v is not None}
            if not params:
                raise api_error(422, "validation_error", "Geen wijziging opgegeven.")
            cmd = await queue_command(db, g.home_id, g, "set_group", params, "user", p.actor)
            await audit(db, p.actor, "light_command", g.name, device=str(g.id), command="set_group", **params)
    except PolicyDenied as e:
        raise api_error(409, "policy_denied", e.message) from e
    await db.commit()
    return JSONResponse({"command_id": str(cmd.id), "status": "queued"}, status_code=202)


# --- buttons ------------------------------------------------------------------


def mapping_conflicts(mappings: list[dict], bridge_rules: set[str]) -> dict[int, str]:
    """index -> conflict message. Pure function, tested. Disabled mappings never conflict."""
    out: dict[int, str] = {}
    by_key: dict[tuple[str, str], list[int]] = {}
    for i, m in enumerate(mappings):
        if m.get("enabled") is False:
            continue
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


def _blocking(conflicts: dict[int, str]) -> dict[int, str]:
    return {i: c for i, c in conflicts.items() if "bevestigen" in c or "Meerdere" in c}


def _mapping_dict(m: ButtonMapping) -> dict:
    return {
        "id": m.id,
        "button": m.button,
        "event": m.event,
        "mode": m.mode,
        "action": m.action,
        "combined_confirmed": m.combined_confirmed,
        "enabled": m.enabled is not False,
    }


def button_view(d: Device, maps: list[ButtonMapping]) -> dict:
    s = d.state or {}
    rules = set(s.get("bridge_rules") or [])
    buttons = [
        {
            "id": b.get("id"),
            "control_id": b.get("control_id"),
            "type": b.get("type", "button"),
            "events": b.get("events") or [],
            "bridge_rule": b.get("id") in rules,
        }
        for b in (s.get("buttons") or [])
        if isinstance(b, dict)
    ]
    as_dicts = [_mapping_dict(m) for m in maps]
    conflicts = mapping_conflicts(as_dicts, rules)
    for i, m in enumerate(as_dicts):
        m["conflict"] = conflicts.get(i)
    return {
        "id": d.id,
        "name": d.name,
        "model": d.model,
        "online": d.online,
        "last_seen": d.last_seen,
        "battery": s.get("battery"),
        "buttons": buttons,
        "events": s.get("events") or ["initial_press", "short_release", "long_press"],
        "bridge_rules": sorted(rules),
        "bridge_rule_names": s.get("bridge_rule_names") or [],
        "last_event": s.get("last_event"),
        "mapping": as_dicts,
    }


async def _maps(db: DB, device_id: uuid.UUID) -> list[ButtonMapping]:
    return list(
        (
            await db.execute(
                select(ButtonMapping)
                .where(ButtonMapping.device_id == device_id)
                .order_by(ButtonMapping.button, ButtonMapping.event)
            )
        ).scalars()
    )


async def _button(db: DB, device_id: uuid.UUID) -> Device:
    d = await db.get(Device, device_id)
    if not d or d.kind != "button":
        raise not_found("Knop")
    return d


@router.get("/homes/{home_id}/buttons")
async def buttons(home_id: uuid.UUID, db: DB, p: Auth) -> list[dict]:
    await get_home(db, home_id)
    devs = (
        await db.execute(
            select(Device)
            .where(Device.home_id == home_id, Device.kind == "button", Device.lifecycle != "archived")
            .order_by(Device.name)
        )
    ).scalars()
    return [button_view(d, await _maps(db, d.id)) for d in list(devs)]


@router.get("/buttons/{device_id}")
async def button(device_id: uuid.UUID, db: DB, p: Auth) -> dict:
    d = await _button(db, device_id)
    return button_view(d, await _maps(db, d.id))


class MappingIn(BaseModel):
    button: str = Field(min_length=1, max_length=ID_MAX)
    event: str = Field(min_length=1, max_length=ID_MAX)
    mode: Literal["lights", "climate", "combined"]
    action: dict[str, Any]
    combined_confirmed: bool = False
    enabled: bool = True


class MappingPatch(BaseModel):
    button: str | None = Field(default=None, min_length=1, max_length=ID_MAX)
    event: str | None = Field(default=None, min_length=1, max_length=ID_MAX)
    mode: Literal["lights", "climate", "combined"] | None = None
    action: dict[str, Any] | None = None
    combined_confirmed: bool | None = None
    enabled: bool | None = None


def _uuid(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


async def normalize_mapping(db: DB, dev: Device, m: dict) -> tuple[dict, list[str]]:
    """Validate one mapping against the button and the home. Returns (normalized action, errors)."""
    errors: list[str] = []
    s = dev.state or {}
    known = {b.get("id"): b for b in (s.get("buttons") or []) if isinstance(b, dict) and b.get("id")}
    if known:
        btn = known.get(m["button"])
        if btn is None:
            errors.append("Deze knop hoort niet bij dit apparaat.")
        elif btn.get("events") and m["event"] not in btn["events"]:
            errors.append(f"Deze knop kent de gebeurtenis ‘{m['event']}’ niet.")
    steps = mapping_steps(m.get("action"))
    if not steps:
        errors.append("Geen actie opgegeven.")
    mode = m["mode"]
    out_steps = []
    for n, step in enumerate(steps, start=1):
        where = f"Stap {n}: "
        if step["type"] == "light":
            if mode == "climate":
                errors.append(where + "een klimaatkoppeling mag alleen airco-stappen bevatten.")
                continue
            target_id = _uuid(step.get("device_id"))
            target = await db.get(Device, target_id) if target_id else None
            if target is None or target.home_id != dev.home_id or target.lifecycle == "archived":
                errors.append(where + "lamp, groep of scène niet gevonden.")
                continue
            allowed = LIGHT_ACTIONS.get(target.kind)
            if not allowed:
                errors.append(where + "alleen lampen, lichtgroepen en scènes kunnen worden bediend.")
                continue
            action = step.get("action") or allowed[0]
            if action not in allowed:
                errors.append(where + f"actie ‘{action}’ past niet bij dit apparaat.")
                continue
            raw = step.get("params") or {}
            params: dict[str, Any] = {}
            if not isinstance(raw, dict):
                errors.append(where + "ongeldige instellingen.")
                continue
            if "on" in raw:
                if not isinstance(raw["on"], bool):
                    errors.append(where + "‘aan’ moet waar of onwaar zijn.")
                    continue
                params["on"] = raw["on"]
            if raw.get("brightness") is not None:
                b = raw["brightness"]
                if isinstance(b, bool) or not isinstance(b, int | float) or not 1 <= b <= 100:
                    errors.append(where + "helderheid moet tussen 1 en 100 liggen.")
                    continue
                params["brightness"] = int(b)
            if action in ("set_group", "set_light") and not params:
                errors.append(where + "kies aan/uit of een helderheid.")
                continue
            norm = {"type": "light", "device_id": str(target.id), "action": action}
            if params and action not in ("toggle", "recall_scene"):
                norm["params"] = params
            out_steps.append(norm)
        else:
            if mode == "lights":
                errors.append(where + "een lichtkoppeling mag alleen lamp-, groep- en scènestappen bevatten.")
                continue
            zone_id = _uuid(step.get("zone_id"))
            zone = await db.get(ClimateZone, zone_id) if zone_id else None
            if zone is None or zone.home_id != dev.home_id:
                errors.append(where + "klimaatzone niet gevonden.")
                continue
            if step.get("action") == "resume":
                out_steps.append({"type": "zone", "zone_id": str(zone.id), "action": "resume"})
                continue
            norm = {"type": "zone", "zone_id": str(zone.id)}
            if step.get("mode") is not None:
                if step["mode"] not in cl.ALL_MODES:
                    errors.append(where + "onbekende modus.")
                    continue
                norm["mode"] = step["mode"]
            if step.get("target_c") is not None:
                t = step["target_c"]
                if isinstance(t, bool) or not isinstance(t, int | float) or not 5 <= t <= 35:
                    errors.append(where + "doeltemperatuur moet tussen 5 en 35 °C liggen.")
                    continue
                norm["target_c"] = float(t)
            if step.get("fan") is not None:
                if step["fan"] not in FAN_SPEEDS:
                    errors.append(where + "onbekende ventilatorstand.")
                    continue
                norm["fan"] = step["fan"]
            if step.get("override_minutes") is not None:
                o = step["override_minutes"]
                if isinstance(o, bool) or not isinstance(o, int) or not 5 <= o <= 1440:
                    errors.append(where + "duur moet tussen 5 en 1440 minuten liggen.")
                    continue
                norm["override_minutes"] = o
            if not any(k in norm for k in ("mode", "target_c", "fan")):
                errors.append(where + "kies een modus, temperatuur of ventilatorstand (of ‘terug naar schema’).")
                continue
            out_steps.append(norm)
    if mode == "combined" and not errors:
        types = {st["type"] for st in out_steps}
        if types != {"light", "zone"}:
            errors.append("Een gecombineerde koppeling heeft zowel een licht- als een airco-stap.")
    return {"steps": out_steps}, errors


def _validation_error(errors: list[str]) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": {"code": "validation_error", "message": " ".join(errors[:5]), "errors": errors}},
    )


def _conflict_error(blocking: dict[int, str]) -> JSONResponse:
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


@router.put("/buttons/{device_id}/mappings")
async def put_mappings(device_id: uuid.UUID, body: list[MappingIn], db: DB, p: Admin):
    """Replace all mappings of a button."""
    d = await _button(db, device_id)
    as_dicts = [m.model_dump() for m in body]
    errors: list[str] = []
    for i, m in enumerate(as_dicts):
        action, errs = await normalize_mapping(db, d, m)
        m["action"] = action
        errors += [f"Koppeling {i + 1}: {e}" for e in errs]
    if errors:
        return _validation_error(errors)
    blocking = _blocking(mapping_conflicts(as_dicts, set((d.state or {}).get("bridge_rules", []))))
    if blocking:
        return _conflict_error(blocking)
    await db.execute(delete(ButtonMapping).where(ButtonMapping.device_id == device_id))
    for m in as_dicts:
        db.add(ButtonMapping(home_id=d.home_id, device_id=device_id, **m))
    await audit(db, p.actor, "button_mappings", d.name, count=len(body))
    await db.commit()
    return button_view(d, await _maps(db, d.id))


async def _save_one(db: DB, d: Device, m: ButtonMapping | None, data: dict, actor: str):
    action, errs = await normalize_mapping(db, d, data)
    if errs:
        return _validation_error(errs)
    data["action"] = action
    others = [_mapping_dict(x) for x in await _maps(db, d.id) if m is None or x.id != m.id]
    blocking = _blocking(mapping_conflicts([*others, data], set((d.state or {}).get("bridge_rules", []))))
    mine = {i: c for i, c in blocking.items() if i == len(others)}
    if mine:
        return _conflict_error({0: next(iter(mine.values()))})
    if m is None:
        m = ButtonMapping(home_id=d.home_id, device_id=d.id, **data)
        db.add(m)
        await audit(db, actor, "button_mapping_added", d.name, button=data["button"], event=data["event"])
    else:
        for k, v in data.items():
            setattr(m, k, v)
        await audit(db, actor, "button_mapping_updated", d.name, mapping=str(m.id))
    await db.commit()
    return button_view(d, await _maps(db, d.id))


@router.post("/buttons/{device_id}/mappings")
async def add_mapping(device_id: uuid.UUID, body: MappingIn, db: DB, p: Admin):
    d = await _button(db, device_id)
    return await _save_one(db, d, None, body.model_dump(), p.actor)


async def _mapping(db: DB, mapping_id: uuid.UUID) -> tuple[ButtonMapping, Device]:
    m = await db.get(ButtonMapping, mapping_id)
    if m is None:
        raise not_found("Knopkoppeling")
    return m, await _button(db, m.device_id)


@router.patch("/button-mappings/{mapping_id}")
async def patch_mapping(mapping_id: uuid.UUID, body: MappingPatch, db: DB, p: Admin):
    m, d = await _mapping(db, mapping_id)
    data = {**_mapping_dict(m)}
    data.pop("id")
    for f in body.model_fields_set:
        v = getattr(body, f)
        if v is not None:
            data[f] = v
    return await _save_one(db, d, m, data, p.actor)


@router.delete("/button-mappings/{mapping_id}", status_code=204)
async def delete_mapping(mapping_id: uuid.UUID, db: DB, p: Admin):
    m, d = await _mapping(db, mapping_id)
    await db.delete(m)
    await audit(db, p.actor, "button_mapping_removed", d.name, mapping=str(mapping_id))
    await db.commit()
    return Response(status_code=204)


class SimulateIn(BaseModel):
    button: str = Field(min_length=1, max_length=ID_MAX)
    event: str = Field(min_length=1, max_length=ID_MAX)


async def _dry_run(db: DB, home: Home, dev: Device, step: dict) -> dict:
    """Run one step's checks inside a savepoint that is always rolled back: nothing is queued."""
    actor = f"knop {dev.name} (simulatie)"
    if step["type"] == "zone":
        zone = await db.get(ClimateZone, _uuid(step.get("zone_id"))) if _uuid(step.get("zone_id")) else None
        if zone is None or zone.home_id != dev.home_id:
            return {"type": "zone", "result": "invalid", "reason": "Klimaatzone niet gevonden."}
        out = {"type": "zone", "zone_id": zone.id, "zone_name": zone.name}
        if step.get("action") == "resume":
            return {**out, "action": "resume", "result": "would_run", "reason": "Terug naar schema."}
        sp = await db.begin_nested()
        try:
            cmd = await request_zone_command(
                db,
                zone,
                home,
                mode=step.get("mode"),
                target_c=step.get("target_c"),
                fan=step.get("fan"),
                override_minutes=step.get("override_minutes"),
                source="button",
                actor=actor,
            )
            out.update(action="set_hvac", params=dict(cmd.params), result="would_run", reason=None)
        except CommandRejected as e:
            out.update(action="set_hvac", result="refused", code=e.code, reason=e.message)
        finally:
            await sp.rollback()
        return out
    target = await db.get(Device, _uuid(step.get("device_id"))) if _uuid(step.get("device_id")) else None
    if target is None or target.home_id != dev.home_id or target.lifecycle == "archived":
        return {"type": "light", "result": "invalid", "reason": "Lamp, groep of scène niet gevonden."}
    action, params = light_command(target, step)
    out = {"type": "light", "device_id": target.id, "device_name": target.name, "action": action, "params": params}
    sp = await db.begin_nested()
    try:
        await queue_command(db, home.id, target, action, params, "button", actor)
        out.update(result="would_run", reason=None)
    except PolicyDenied as e:
        out.update(result="refused", code="policy_denied", reason=e.message)
    finally:
        await sp.rollback()
    return out


@router.post("/buttons/{device_id}/simulate")
async def simulate(device_id: uuid.UUID, body: SimulateIn, db: DB, p: Admin) -> dict:
    """What a press would do now: each mapping's steps with the same checks as a real press
    (safety policy, outdoor unit, "choose a mode first"), plus conflicts. Executes nothing."""
    d = await _button(db, device_id)
    home = await get_home(db, d.home_id)
    maps = await _maps(db, d.id)
    rules = set((d.state or {}).get("bridge_rules") or [])
    as_dicts = [_mapping_dict(m) for m in maps]
    conflicts = mapping_conflicts(as_dicts, rules)
    results = []
    for i, m in enumerate(maps):
        if m.button != body.button or m.event != body.event:
            continue
        entry: dict[str, Any] = {"mapping_id": m.id, "mode": m.mode, "conflict": conflicts.get(i), "steps": []}
        if m.enabled is False:
            entry["skipped"] = "Deze koppeling staat uit."
        elif m.mode == "combined" and not m.combined_confirmed:
            entry["skipped"] = "Gecombineerde actie is nog niet bevestigd en wordt niet uitgevoerd."
        else:
            for step in mapping_steps(m.action):
                if (step["type"] == "zone" and m.mode == "lights") or (step["type"] == "light" and m.mode == "climate"):
                    entry["steps"].append(
                        {"type": step["type"], "result": "invalid", "reason": "Stap past niet bij de koppeling."}
                    )
                    continue
                entry["steps"].append(await _dry_run(db, home, d, step))
        results.append(entry)
    await db.rollback()
    bridge = body.button in rules
    would_run = any(s.get("result") == "would_run" for e in results for s in e["steps"])
    if not results:
        message = "Aan deze knopgebeurtenis is in de app niets gekoppeld."
    elif would_run:
        message = "Een druk voert de getoonde stappen uit."
    else:
        message = "Een druk zou nu niets uitvoeren; zie de reden per stap."
    if bridge:
        message += " Let op: de Hue Bridge voert voor deze knop zelf ook een actie uit."
    return {
        "button": body.button,
        "event": body.event,
        "would_run": would_run,
        "bridge_rule": bridge,
        "mappings": results,
        "message": message,
    }
