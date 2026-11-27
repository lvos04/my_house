"""Expliciet apparaatherstel door de lokale beheerder van de oorspronkelijke Hub-koppeling."""

import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .. import connector
from .. import connector_changes as changes
from ..contract_http import AdminContractRoute, ContractProblem, contract_error
from ..deps import DB, WebAdmin
from .auth import client_ip, password_matches
from .connector_routes import ERRORS as CONNECTOR_ERRORS
from .connector_routes import Receipt
from .hub_key_management import AUTH_ERRORS

router = APIRouter(
    prefix="/system/hub-recovery", tags=["system"], route_class=AdminContractRoute,
    responses={**AUTH_ERRORS, 404: {"model": ContractProblem}, 422: {"model": ContractProblem}},
)


class RecoveryConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    password: SecretStr = Field(min_length=1, max_length=256)


@router.get("/{proposal_id}")
async def hub_recovery_preview(proposal_id: uuid.UUID, db: DB, p: WebAdmin, response: Response) -> dict:
    """Lees de oorspronkelijke metingen en de actuele herstelpreview."""
    change = await changes.owned_hub_change(db, proposal_id, p.user.id)
    response.headers["Cache-Control"] = "no-store"
    proof = await changes.evidence(db, change.client, change.id)
    return {**proof, "target": change.target}


@router.post(
    "/{proposal_id}", status_code=202, response_model=Receipt,
    responses={**CONNECTOR_ERRORS, 429: {"model": ContractProblem}},
)
async def confirm_hub_recovery(
    proposal_id: uuid.UUID, body: RecoveryConfirmation, request: Request, response: Response, db: DB, p: WebAdmin,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    """Bevestig de getoonde terugzetstand met het eigen beheerderswachtwoord."""
    await connector.lock_verified_pin(db)
    change = await changes.owned_hub_change(db, proposal_id, p.user.id)
    if not await password_matches(db, p.user, body.password.get_secret_value(), ip=client_ip(request)):
        await db.commit()
        raise contract_error(403, "wrong_password", "De wachtwoordbevestiging voor het herstel is mislukt.")

    scope = f"POST /system/hub-recovery web:{p.user.id}"
    digest = hashlib.sha256(f"{change.id}/{body.preview_hash}".encode()).digest()
    key, stored = await connector.bounded_reservation(
        db, lambda: connector.reserve_idempotency(db, scope, idempotency_key, digest),
    )
    if stored is None:
        status, result = await changes.local_rollback(db, change, body.preview_hash, p.user)
        await connector.record_idempotency(db, scope, key, status, result)
    else:
        status, result = stored
        response.headers["Idempotent-Replay"] = "true"
    await db.commit()
    response.status_code = status
    return result
