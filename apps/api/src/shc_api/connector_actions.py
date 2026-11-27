"""Witte lijst en doelvalidatie van de eigenaar-connector."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from . import climate
from .commanding import check_active_target, check_scene_group_policy
from .commissioning import command_facts, lock_home_commands
from .connector import MAX_PARAMETER_BYTES
from .contract_http import contract_error
from .models import ClimateZone, Device, Home
from .policy import MEASURE_ONLY_KINDS, PolicyDenied, check_command
from .routers.zones import Block, ZoneCommand, validate_blocks
from .views.common import caps_of, other_zone_modes


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GroupParameters(Empty):
    on: bool | None = None
    brightness: int | None = Field(default=None, ge=1, le=100)


class SocketParameters(Empty):
    on: bool


class ZoneParameters(ZoneCommand):
    model_config = ConfigDict(extra="forbid", strict=True)


class ScheduleBlockParameters(Block):
    model_config = ConfigDict(extra="forbid", strict=True)


class ScheduleParameters(Empty):
    blocks: list[ScheduleBlockParameters] = Field(max_length=100)


@dataclass(frozen=True)
class Action:
    target_type: str
    target_field: str
    command: str
    schema: type[BaseModel]
    risk: str


SOCKET_PROPOSAL = "socket.relay.propose"
ACTIONS = {
    "light.group.set": Action("light_group", "group_id", "set_group", GroupParameters, "R1"),
    "light.scene.recall": Action("scene", "scene_id", "recall_scene", Empty, "R1"),
    "zone.command": Action("zone", "zone_id", "set_hvac", ZoneParameters, "R2"),
    "zone.resume": Action("zone", "zone_id", "set_hvac", Empty, "R2"),
    "zone.schedule.propose": Action("zone", "zone_id", "set_hvac", ScheduleParameters, "R2"),
    SOCKET_PROPOSAL: Action("energy_socket", "device_id", SOCKET_PROPOSAL, SocketParameters, "R1"),
}


def discovery(zone_unit: Device | None) -> list[dict]:
    caps = caps_of(zone_unit)
    actions = []
    for action_id, action in ACTIONS.items():
        schema = action.schema.model_json_schema()
        schema["x-max-canonical-bytes"] = MAX_PARAMETER_BYTES
        if action.schema is ZoneParameters:
            schema["properties"]["mode"] = {"enum": [*caps.modes, None]}
            schema["properties"]["target_c"] = {
                "anyOf": [{"type": "integer", "minimum": caps.min_c, "maximum": caps.max_c}, {"type": "null"}],
            }
            if caps.fan is False:
                schema["properties"].pop("fan")
        if action.schema in (ZoneParameters, GroupParameters):
            schema["anyOf"] = [
                {"required": [field], "properties": {field: {"not": {"type": "null"}}}}
                for field in schema["properties"] if field != "override_minutes"
            ]
        if action.schema is ScheduleParameters:
            block = schema["$defs"]["ScheduleBlockParameters"]["properties"]
            block["days"]["uniqueItems"] = True
            block["days"]["items"].update(minimum=0, maximum=6)
            block["mode"] = {"enum": [*caps.modes, None]}
            block["target_c"] = {
                "type": "integer", "minimum": max(10, caps.min_c), "maximum": min(30, caps.max_c),
            }
        actions.append({
            "action_id": action_id, "target_type": action.target_type, "target_field": action.target_field,
            "params_schema": schema, "min_role": "control",
        })
    return actions


@dataclass(frozen=True)
class Target:
    action: Action
    zone: ClimateZone | None
    device: Device | None
    params: dict


def not_found():
    return contract_error(404, "not_found", "Connectordoel of bewijs niet gevonden.")


async def checked_resource(
    db: AsyncSession, action: Action, target_id: uuid.UUID,
) -> tuple[ClimateZone | None, Device | None]:
    """Dezelfde doel- en inbedrijfstellingscontrole voor discover, propose en apply."""
    zone = await db.get(ClimateZone, target_id) if action.target_type == "zone" else None
    device = await db.get(Device, target_id) if zone is None else None
    resource = zone or device
    if resource is None:
        raise not_found()
    await lock_home_commands(db, resource.home_id)
    await db.refresh(resource)
    if zone is not None and zone.unit_device_id is not None:
        device = await db.get(Device, zone.unit_device_id, populate_existing=True)
    if device is not None:
        await check_target_policy(db, device, action, resource.home_id)
    else:
        home = await db.get(Home, resource.home_id, populate_existing=True)
        if home is None or home.active_to is not None:
            raise not_found()
    if action.target_type == "zone":
        if zone is None or (device is not None and device.kind != "ac_unit"):
            raise not_found()
    elif device is None or device.kind != action.target_type:
        raise not_found()
    if action.command == "set_hvac" and action.schema is not ScheduleParameters and device is None:
        raise contract_error(409, "device_not_tested", "Aan deze zone is geen geteste klimaatunit gekoppeld.")
    return zone, device


async def resolve_target(db: AsyncSession, proposal: dict, risk: str) -> Target:
    action = ACTIONS.get(proposal["action_id"])
    if action is None:
        raise contract_error(422, "action_not_allowed", "Deze actie staat niet op de lijst toegestane acties.")
    target = proposal["target"]
    try:
        if set(target) != {action.target_field}:
            raise ValueError
        target_id = uuid.UUID(target[action.target_field])
        if str(target_id) != target[action.target_field]:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise contract_error(422, "validation_error", "Geef precies één geldig doel op.") from None
    zone, device = await checked_resource(db, action, target_id)
    if risk < action.risk:
        raise contract_error(403, "risk_class_too_low", "De ondertekende risicoklasse is te laag voor deze actie.")

    try:
        parsed = action.schema.model_validate(proposal["parameters"])
    except ValidationError:
        raise contract_error(422, "validation_error", "De parameters voldoen niet aan het actieschema.") from None
    params = parsed.model_dump(exclude_none=True, exclude_unset=True)
    if action.schema is ScheduleParameters:
        try:
            validate_blocks(parsed.blocks, device)
        except HTTPException as exc:
            raise contract_error(422, "validation_error", exc.detail["message"]) from None
    if action.schema is GroupParameters and not params:
        raise contract_error(422, "validation_error", "Geef een schakelstand of helderheid op.")
    if action.schema is ZoneParameters:
        if not {"mode", "target_c", "fan"} & params.keys():
            raise contract_error(422, "validation_error", "Geen wijziging opgegeven.")
        caps = caps_of(device)
        if "fan" in proposal["parameters"] and caps.fan is False:
            raise contract_error(422, "validation_error", "Deze unit heeft geen instelbare ventilator.")
        reason = climate.validate_request(caps, await other_zone_modes(db, zone, now=None),
                                          params.get("mode"), params.get("target_c"))
        if reason:
            raise contract_error(409, "policy_denied", reason)
    return Target(action, zone, device, params)


async def check_target_policy(db: AsyncSession, device: Device, action: Action, home_id: uuid.UUID) -> None:
    if device.home_id != home_id:
        raise contract_error(409, "policy_denied", "Dit apparaat hoort niet bij de woning van dit connectordoel.")
    try:
        await check_scene_group_policy(db, device, action.command, {}, safe_mode=True)
        facts = await command_facts(db, device, safe_mode=True)
        # Voor connectorbediening geldt de geteste status ook voor eerder geïnstalleerde apparaten.
        check_command(replace(facts, commissioning_gate=False), action.command, {})
        socket_concept = (action.command == SOCKET_PROPOSAL and facts.kind == "energy_socket"
                          and device.provider == "homewizard")
        if facts.kind in MEASURE_ONLY_KINDS and not socket_concept:
            raise PolicyDenied("Dit doel wordt alleen gebruikt om te meten.")
        check_command(replace(facts, commissioning_gate=True), action.command, {})
        await check_active_target(db, home_id, device, climate=action.command == "set_hvac")
    except PolicyDenied as exc:
        raise contract_error(409, exc.code, exc.message) from None
