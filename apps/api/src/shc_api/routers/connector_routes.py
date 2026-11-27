"""Schrijfconnector voor de Hub met behoud van het bestaande API-foutformaat."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .. import connector_changes as changes
from ..connector import process_request
from ..connector_actions import ACTIONS, SOCKET_PROPOSAL, checked_resource, discovery, not_found
from ..contract_http import ConnectorContractRoute, ContractProblem, contract_error
from ..deps import DB, Auth, ConnectorWriter, Principal
from ..models import ClimateZone, Device

router = APIRouter(prefix="/connector/v1", tags=["connector"], route_class=ConnectorContractRoute)
HUB_ACTIES = frozenset({
    "light.group.set", "light.scene.recall", "zone.command", "zone.resume", "zone.schedule.propose",
})
ERRORS = {status: {"model": ContractProblem} for status in (400, 401, 403, 404, 409, 413, 422, 501)}
ERRORS[503] = {
    "model": ContractProblem,
    "description": "dependency_unavailable of connector_pins_unavailable",
    "headers": {"Retry-After": {"schema": {"type": "integer"}, "description": "Wachttijd in seconden."}},
}
Idem = Annotated[str | None, Header(alias="Idempotency-Key", description="Verplichte UUID per verzoek.")]


class ExecutionEnvelope(BaseModel):
    # De verificatiekern beoordeelt de originele JSON, inclusief alle onbekende velden.
    model_config = ConfigDict(extra="forbid", strict=True)
    proposal: dict
    policy_decision: dict
    approval: dict | None
    issued_at: str
    expires_at: str
    nonce: str
    signature: str


class DiscoveredAction(BaseModel):
    action_id: str
    target_type: str
    target_field: str
    params_schema: dict
    min_role: str
    max_age_s: int


class Discovery(BaseModel):
    actions: list[DiscoveredAction]
    checked_target: dict[str, uuid.UUID] | None = None


class Preview(BaseModel):
    proposal_id: uuid.UUID
    preview_hash: str
    resource_etag: str
    diff: dict
    before: dict | None
    blast_radius: list[dict]
    risk_claim: str
    rollback_recipe: dict | None
    requires_backup: bool
    observed_at: datetime | None
    valid_until: datetime
    status: str
    reason_code: str | None
    concept_only: bool


class Receipt(BaseModel):
    command_id: uuid.UUID
    evidence_id: uuid.UUID
    status: str


def _client(principal: Principal) -> str:
    return f"client:{principal.client_id}" if principal.client_id else f"user:{principal.user.id}"


async def _mutation(db, principal, idem, body, operation):
    client = _client(principal)
    if principal.client_purpose == "hub":
        action_id = body["proposal"].get("action_id")
        if not isinstance(action_id, str) or action_id not in HUB_ACTIES:
            raise contract_error(403, "forbidden", "Deze actie valt buiten de gekoppelde Hub-bediening.")

    async def execute(session, verified):
        return await operation(session, verified, client)

    status, data, replay = await process_request(
        db,
        scope=f"POST {router.prefix}/{operation.__name__} {client}",
        idempotency_key=idem,
        request=body,
        operation=execute,
    )
    if replay and (principal.client_purpose == "hub" or
                   operation is changes.propose and body["proposal"]["action_id"] == SOCKET_PROPOSAL):
        # Ook bij herhaling gelden de huidige beschermde rollen en inbedrijfstellingsstatus.
        await changes.check_replayed_target(db, body["proposal"], body["policy_decision"]["risk_class"], client)
    await db.commit()
    headers = {"Idempotent-Replay": "true"} if replay else {}
    if status == 200 and "resource_etag" in data:
        headers["ETag"] = f'"{data["resource_etag"]}"'
    return JSONResponse(data, status, headers=headers)


@router.get("/discover", response_model=Discovery, response_model_exclude_none=True, responses=ERRORS)
async def discover(
    db: DB, _p: Auth, zone_id: uuid.UUID | None = None,
    action_id: str | None = None, target_id: uuid.UUID | None = None,
) -> dict:
    device = None
    checked = None
    if action_id is not None or target_id is not None:
        action = ACTIONS.get(action_id)
        if action is None or target_id is None or zone_id is not None:
            raise contract_error(422, "validation_error", "Geef één bekende actie en haar doel op.")
        _, device = await checked_resource(db, action, target_id)
        checked = {action.target_field: target_id}
    elif zone_id is not None:
        zone = await db.get(ClimateZone, zone_id)
        if zone is None:
            raise not_found()
        if zone.unit_device_id:
            device = await db.get(Device, zone.unit_device_id)
    return {"actions": [{**a, "max_age_s": changes.MAX_AGE_S} for a in discovery(device)
                        if action_id is None or a["action_id"] == action_id], "checked_target": checked}


@router.post("/propose", response_model=Preview, responses=ERRORS)
async def propose(db: DB, p: ConnectorWriter, body: ExecutionEnvelope, idempotency_key: Idem = None) -> Response:
    return await _mutation(db, p, idempotency_key, body.model_dump(), changes.propose)


@router.post("/apply", status_code=202, response_model=Receipt, responses=ERRORS)
async def apply(db: DB, p: ConnectorWriter, body: ExecutionEnvelope, idempotency_key: Idem = None) -> Response:
    return await _mutation(db, p, idempotency_key, body.model_dump(), changes.apply)


@router.post("/rollback", status_code=202, response_model=Receipt, responses=ERRORS)
async def rollback(db: DB, p: Auth, body: ExecutionEnvelope, idempotency_key: Idem = None) -> Response:
    return await _mutation(db, p, idempotency_key, body.model_dump(), changes.rollback)


@router.get("/evidence/{evidence_id}", responses=ERRORS)
async def evidence(db: DB, p: Auth, evidence_id: uuid.UUID) -> dict:
    return await changes.evidence(db, _client(p), evidence_id)
