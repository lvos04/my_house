"""Beheer van de lokaal gepinde publieke Hub-sleutels."""

import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import connector
from ..commissioning import lock_commissioning_shared
from ..contract_http import AdminContractRoute, ContractProblem
from ..deps import DB, WebAdmin
from ..store import audit

router = APIRouter(prefix="/system/hub-keys", tags=["system"], route_class=AdminContractRoute)

AUTH_ERRORS = {
    401: {"model": ContractProblem},
    403: {"model": ContractProblem},
    503: {
        "model": ContractProblem,
        "description": "`dependency_unavailable`: de database is tijdelijk niet beschikbaar.",
        "headers": {"Retry-After": {"schema": {"type": "integer"}}},
    },
}
CHECK_ERRORS = {
    **AUTH_ERRORS,
    409: {"model": ContractProblem},
    412: {"model": ContractProblem},
    413: {"model": ContractProblem},
    422: {"model": ContractProblem},
    428: {"model": ContractProblem},
    501: {"model": ContractProblem},
}
IF_MATCH = {"name": "If-Match", "in": "header", "required": True, "schema": {"type": "string"}}
IDEMPOTENCY = {
    "name": "Idempotency-Key", "in": "header", "required": True, "schema": {"type": "string", "format": "uuid"},
}


class PinInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: connector.PinMode
    source: str = Field(min_length=1, max_length=131_072)


class PinConfirmation(PinInput):
    fingerprint: str = Field(min_length=1, max_length=128)


class PinCandidate(BaseModel):
    fingerprint: str
    sequence: str


class HubPinStatus(BaseModel):
    state: Literal["empty", "damaged", "pinned"]
    fingerprint: str | None = None
    sequence: str | None = None


@router.get("", response_model_exclude_none=True, responses=AUTH_ERRORS)
async def hub_pin_status(db: DB, _p: WebAdmin, response: Response) -> HubPinStatus:
    pin = await connector.read_managed_pin(db)
    response.headers["ETag"] = pin.etag
    response.headers["Cache-Control"] = "no-store"
    return HubPinStatus.model_validate(pin.payload())


@router.post("/verify", responses=CHECK_ERRORS, openapi_extra={"parameters": [IF_MATCH]})
async def verify_hub_keys(db: DB, _p: WebAdmin, request: Request, body: PinInput) -> PinCandidate:
    candidate = await connector.preview_managed_pin(db, request.headers.get("If-Match"), body.mode, body.source)
    return PinCandidate(fingerprint=candidate.fingerprint, sequence=str(candidate.sequence))


@router.put(
    "", response_model_exclude_none=True, responses={400: {"model": ContractProblem}, **CHECK_ERRORS},
    openapi_extra={"parameters": [IF_MATCH, IDEMPOTENCY]},
)
async def confirm_hub_keys(
    db: DB, p: WebAdmin, request: Request, response: Response, body: PinConfirmation,
) -> HubPinStatus:
    # Logisch herstel wist ook deze ontvangstbewijzen. Houd de gedeelde installatielock tot de commit vast.
    await lock_commissioning_shared(db)
    etag = request.headers.get("If-Match")
    scope = f"PUT /system/hub-keys web:{p.user.id}"
    digest = hashlib.sha256(json.dumps([etag, body.model_dump()], sort_keys=True).encode()).digest()
    key, replay = await connector.reserve_idempotency(db, scope, request.headers.get("Idempotency-Key"), digest)
    if replay is None:
        pin = await connector.save_managed_pin(db, etag, body.mode, body.source, body.fingerprint)
        result = pin.payload()
        await audit(db, p.actor, "hub_keys_confirmed", "system", mode=body.mode, sequence=result["sequence"])
        await connector.record_idempotency(db, scope, key, 200, {"pin": result, "etag": pin.etag})
        await db.commit()
        response.headers["ETag"] = pin.etag
    else:
        _, previous = replay
        result = previous["pin"]
        response.headers["ETag"] = previous["etag"]
        response.headers["Idempotent-Replay"] = "true"
    request.state.domain_audit = True
    response.headers["Cache-Control"] = "no-store"
    return HubPinStatus.model_validate(result)
