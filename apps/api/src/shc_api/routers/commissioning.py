"""Beheer van inbedrijfstelling en de apparaathistorie."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select, text, tuple_

from ..commanding import check_scene_group_policy
from ..commissioning import (
    KEY,
    TEST_CONFIRMED_ACTION,
    confirmation_block,
    effective_compatibility,
    is_protected,
    lock_commissioning,
    read_gate,
    record_test_confirmation,
)
from ..contract_http import AdminContractRoute, ContractProblem, contract_error
from ..deps import DB, Admin, get_home
from ..errors import not_found
from ..models import AuditLog, CommandRecord, Device, Home, Room
from ..policy import DeviceNotTested, PolicyDenied
from ..store import audit, get_setting, set_setting
from ..views.lights import LIGHT_ACTIONS

router = APIRouter(
    prefix="/commissioning",
    route_class=AdminContractRoute,
    responses={
        **{status: {"model": ContractProblem} for status in (401, 403, 413, 422)},
        "default": {
            "model": ContractProblem,
            "description": "Overige contractfouten, waaronder een tijdelijke databasefout met Retry-After.",
        },
    },
)
NOT_FOUND = {404: {"model": ContractProblem}}
ETAG = {"description": "Gebruik deze ETag in If-Match bij de bijbehorende mutatie.", "schema": {"type": "string"}}
READ_RESPONSES = {200: {"headers": {"ETag": ETAG}}}
MUTATION_RESPONSES = {
    **{status: {"model": ContractProblem} for status in (400, 404, 409, 412, 428)},
    200: {"headers": {
        "ETag": ETAG,
        "Idempotent-Replay": {
            "description": "Aanwezig bij een herhaling.", "schema": {"type": "string", "enum": ["true"]},
        },
    }},
}
MUTATION_OPENAPI = {"parameters": [
    {
        "name": "Idempotency-Key", "in": "header", "required": True,
        "description": "UUID per handeling. Herhaal met dezelfde sleutel en invoer voor het oorspronkelijke antwoord.",
        "schema": {"type": "string", "format": "uuid"},
    },
    {
        "name": "If-Match", "in": "header", "required": True,
        "description": "ETag uit het bijbehorende GET-verzoek. Mag ontbreken bij een idempotente herhaling.",
        "schema": {"type": "string"},
    },
]}
MAX_PAGE = 200


class Gate(BaseModel):
    version: int
    enabled: bool
    known: bool
    origin: Literal["fresh", "existing_devices", "unknown"]
    enabled_at: str | None
    enabled_by: str | None


class GateView(BaseModel):
    gate: Gate
    task_required: bool
    untested_count: int
    unsupported_count: int
    affected_count: int


class ConfirmedCommand(BaseModel):
    action: str
    confirmed_at: datetime


class DeviceReview(BaseModel):
    id: uuid.UUID
    home_id: uuid.UUID
    home_name: str
    room_name: str | None
    name: str
    kind: str
    lifecycle: str
    compatibility: str
    tested_at: datetime | None
    protected: bool
    blocked_when_enabled: bool
    confirmation_allowed: bool
    bulk_confirmation_allowed: bool


class DeviceView(DeviceReview):
    last_confirmed_command: ConfirmedCommand | None = None


class DevicePage(BaseModel):
    items: list[DeviceView]
    next_cursor: uuid.UUID | None
    total: int
    observed_at: datetime
    gate_etag: str


class BulkResult(BaseModel):
    items: list[DeviceView]


class HistoryItem(BaseModel):
    id: int
    at: datetime
    actor: str
    action: Literal["device_marked_tested"]
    status: Literal["tested"]
    model: str | None
    firmware: str | None


class HistoryPage(BaseModel):
    items: list[HistoryItem]
    next_cursor: int | None


class ExplicitConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def require_true(cls, value):
        if value is not True:
            raise ValueError("Bevestig deze handeling uitdrukkelijk.")
        return value


class DeviceConfirmation(ExplicitConfirmation):
    note: str | None = Field(default=None, max_length=500)


class BulkConfirmation(DeviceConfirmation):
    device_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_PAGE)

    @field_validator("device_ids")
    @classmethod
    def unique_devices(cls, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(ids)) != len(ids):
            raise ValueError("Selecteer elk apparaat maar één keer.")
        return ids


def _fingerprint(value) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _etag(value) -> str:
    return f'"{_fingerprint(value)}"'


async def _mutation_headers(request: Request, p: Admin) -> tuple[uuid.UUID, str | None]:
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key is None:
        raise contract_error(400, "idempotency_key_required", "Stuur een Idempotency-Key mee.")
    try:
        key = uuid.UUID(idempotency_key)
    except ValueError:
        raise contract_error(400, "idempotency_key_invalid", "De Idempotency-Key moet een UUID zijn.") from None
    return key, request.headers.get("If-Match")


MutationHeaders = Annotated[tuple[uuid.UUID, str | None], Depends(_mutation_headers)]


async def _prepare(
    db: DB, p: Admin, request: Request, headers: MutationHeaders, body: ExplicitConfirmation,
) -> tuple[str, str, JSONResponse | None]:
    key = "commissioning_req_" + _fingerprint([str(p.user.id), str(p.client_id), str(headers[0])])[:46]
    acquired = await db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
    if not acquired:
        raise contract_error(409, "idempotency_in_progress", "Deze handeling wordt al verwerkt.")
    await lock_commissioning(db)
    # Bewaar een vingerafdruk van de invoer; formulierinhoud en sleutels staan nooit in de audit.
    fingerprint = _fingerprint([request.url.path, body.model_dump(mode="json", exclude_unset=True)])
    receipt = await get_setting(db, key)
    replay = None
    if receipt is not None:
        if fingerprint != receipt["fingerprint"]:
            raise contract_error(422, "idempotency_key_reused", "Deze sleutel hoort al bij andere invoer.")
        replay = JSONResponse(
            receipt["result"], headers={"ETag": receipt["etag"], "Idempotent-Replay": "true"},
        )
    request.state.domain_audit = True
    return key, fingerprint, replay


def _precondition(headers: MutationHeaders, current_etag: str) -> None:
    if headers[1] is None:
        raise contract_error(428, "precondition_required", "Haal de actuele toestand op en stuur If-Match mee.")
    if headers[1] != current_etag:
        raise contract_error(
            412, "precondition_failed", "De toestand is gewijzigd. Haal het overzicht opnieuw op.",
            huidige_etag=current_etag,
        )


async def _finish(db: DB, key: str, fingerprint: str, result: BaseModel, etag: str) -> JSONResponse:
    payload = result.model_dump(mode="json")
    await set_setting(db, key, {"fingerprint": fingerprint, "result": payload, "etag": etag})
    await db.commit()
    return JSONResponse(payload, headers={"ETag": etag})


async def _review(db: DB, device: Device, home: Home, room_name: str | None) -> DeviceReview:
    protected = await is_protected(db, device)
    allowed = confirmation_block(device, home) is None
    compatibility = effective_compatibility(device)
    blocked = False
    if not protected and device.lifecycle == "active" and home.active_to is None:
        if device.kind in {"light", "ac_unit"}:
            blocked = compatibility != "tested"
        elif device.kind in {"light_group", "scene"}:
            try:
                await check_scene_group_policy(db, device, LIGHT_ACTIONS[device.kind][0], {}, preview_enabled=True)
            except DeviceNotTested:
                blocked = True
            except PolicyDenied:
                pass  # Een al beschermd groepsdoel krijgt er geen gateblokkade bij.
            else:
                blocked = compatibility != "tested"
    return DeviceReview(
        id=device.id, home_id=home.id, home_name=home.name, room_name=room_name, name=device.name,
        kind=device.kind, lifecycle=device.lifecycle, compatibility=compatibility, tested_at=device.tested_at,
        protected=protected, blocked_when_enabled=blocked, confirmation_allowed=allowed,
        bulk_confirmation_allowed=allowed and not protected,
    )


def _device_query():
    return select(Device, Home, Room.name).join(Home, Home.id == Device.home_id).outerjoin(
        Room, (Room.id == Device.room_id) & (Room.home_id == Device.home_id),
    )


def _gate_etag(view: GateView, devices: list[DeviceReview]) -> str:
    return _etag([view.model_dump(mode="json"), [d.model_dump(mode="json") for d in devices]])


async def _gate_preview(db: DB) -> tuple[GateView, str, list[DeviceReview]]:
    state = await read_gate(db)
    rows = (await db.execute(_device_query().where(Home.active_to.is_(None), Device.lifecycle == "active")
                             .order_by(Device.id))).all()
    devices = [await _review(db, device, home, room) for device, home, room in rows]
    view = GateView(
        gate=Gate(
            version=state["version"], enabled=state["enabled"], known=state["known"], origin=state["origin"],
            enabled_at=state.get("enabled_at") if isinstance(state.get("enabled_at"), str) else None,
            enabled_by=state.get("enabled_by") if isinstance(state.get("enabled_by"), str) else None,
        ),
        task_required=not state["enabled"],
        untested_count=sum(d.compatibility not in {"tested", "unsupported"} for d in devices),
        unsupported_count=sum(d.compatibility == "unsupported" for d in devices),
        affected_count=sum(d.blocked_when_enabled for d in devices),
    )
    return view, _gate_etag(view, devices), devices


async def _last_confirmed(db: DB, devices: list[DeviceReview]) -> dict[uuid.UUID, ConfirmedCommand]:
    if not devices:
        return {}
    cmd = CommandRecord
    stmt = (
        select(cmd.device_id, cmd.action, cmd.updated_at)
        .where(cmd.status == "confirmed", tuple_(cmd.home_id, cmd.device_id).in_([(d.home_id, d.id) for d in devices]))
        .distinct(cmd.device_id)
        .order_by(cmd.device_id, cmd.updated_at.desc(), cmd.id.desc())
    )
    return {i: ConfirmedCommand(action=action, confirmed_at=at) for i, action, at in (await db.execute(stmt)).all()}


async def _views(db: DB, devices: list[DeviceReview]) -> list[DeviceView]:
    last = await _last_confirmed(db, devices)
    return [DeviceView(**d.model_dump(), last_confirmed_command=last.get(d.id)) for d in devices]


async def _find_device(db: DB, device_id: uuid.UUID) -> tuple[Device, Home, str | None]:
    row = (await db.execute(_device_query().where(Device.id == device_id))).one_or_none()
    if row is None:
        raise not_found("Apparaat")
    device, home, room = row
    return device, home, room


def _check_confirmation(review: DeviceReview, device: Device, home: Home, *, bulk: bool) -> None:
    if review.confirmation_allowed and (not bulk or review.bulk_confirmation_allowed):
        return
    reason = confirmation_block(device, home) or "protected_device"
    messages = {
        "inactive_device": "Dit apparaat of de bijbehorende woning is niet actief.",
        "unsupported_device": "Los de protocolfout op en lees het apparaat opnieuw uit vóór bevestiging.",
        "protected_device": "Een beschermd apparaat kan alleen afzonderlijk voor meten worden bevestigd.",
    }
    raise contract_error(409, "conflict", messages[reason], reason=reason, device_id=str(device.id))


@router.get("", responses=READ_RESPONSES)
async def get_commissioning(db: DB, p: Admin, response: Response) -> GateView:
    """Geef de installatiebrede toestand, beheerderstaak en aantallen voor de inschakeldialoog."""
    await lock_commissioning(db)
    state, etag, _ = await _gate_preview(db)
    response.headers["ETag"] = etag
    return state


@router.get("/devices", responses=NOT_FOUND)
async def list_review_devices(
    db: DB, p: Admin, home_id: uuid.UUID | None = None, affected_only: bool = False,
    cursor: uuid.UUID | None = None, limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
) -> DevicePage:
    """Toon per actief apparaat de teststatus, bescherming en laatste bevestigde bediening."""
    await lock_commissioning(db)
    if home_id is not None:
        await get_home(db, home_id)
    _, gate_etag, devices = await _gate_preview(db)
    selected = [d for d in devices if (home_id is None or d.home_id == home_id)
                and (not affected_only or d.blocked_when_enabled)]
    page = [d for d in selected if cursor is None or d.id > cursor][:limit + 1]
    return DevicePage(
        items=await _views(db, page[:limit]),
        next_cursor=page[limit - 1].id if len(page) > limit else None,
        total=len(selected),
        observed_at=datetime.now(UTC),
        gate_etag=gate_etag,
    )


@router.get("/devices/{device_id}", responses={**NOT_FOUND, **READ_RESPONSES})
async def get_review_device(device_id: uuid.UUID, db: DB, p: Admin, response: Response) -> DeviceView:
    """Haal één apparaat op met de ETag voor een individuele bevestiging."""
    await lock_commissioning(db)
    device, home, room = await _find_device(db, device_id)
    view, = await _views(db, [await _review(db, device, home, room)])
    response.headers["ETag"] = _etag(view.model_dump(mode="json"))
    return view


@router.get("/devices/{device_id}/history", responses=NOT_FOUND)
async def test_history(
    device_id: uuid.UUID, db: DB, p: Admin, limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    cursor: Annotated[int | None, Query(gt=0, le=(1 << 63) - 1)] = None,
) -> HistoryPage:
    """Geef de testbevestigingen voor dit apparaat, inclusief oude regels zonder woningveld."""
    device, _, _ = await _find_device(db, device_id)
    stmt = select(AuditLog).where(
        AuditLog.action == TEST_CONFIRMED_ACTION, AuditLog.detail["device"].astext == str(device.id),
        or_(AuditLog.detail["home"].astext.is_(None), AuditLog.detail["home"].astext == str(device.home_id)),
    )
    if cursor is not None:
        stmt = stmt.where(AuditLog.id < cursor)
    rows = (await db.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit + 1))).all()
    return HistoryPage(
        items=[
            HistoryItem(
                id=row.id, at=row.at, actor=row.actor, action=TEST_CONFIRMED_ACTION, status="tested",
                model=row.detail.get("model") if isinstance(row.detail.get("model"), str) else None,
                firmware=row.detail.get("firmware") if isinstance(row.detail.get("firmware"), str) else None,
            )
            for row in rows[:limit]
        ],
        next_cursor=rows[limit - 1].id if len(rows) > limit else None,
    )


@router.post("/devices/{device_id}/confirm", response_model=DeviceView,
             responses=MUTATION_RESPONSES, openapi_extra=MUTATION_OPENAPI)
async def confirm_device(
    device_id: uuid.UUID, body: DeviceConfirmation, db: DB, p: Admin, request: Request, headers: MutationHeaders,
) -> JSONResponse:
    """Bevestig één apparaat; bij een beschermd apparaat geldt dit alleen voor meten."""
    key, fingerprint, replay = await _prepare(db, p, request, headers, body)
    if replay is not None:
        return replay
    device, home, room = await _find_device(db, device_id)
    review = await _review(db, device, home, room)
    before, = await _views(db, [review])
    _precondition(headers, _etag(before.model_dump(mode="json")))
    _check_confirmation(review, device, home, bulk=False)
    await record_test_confirmation(db, device, actor=p.actor, username=p.user.username, note=body.note)
    result, = await _views(db, [await _review(db, device, home, room)])
    return await _finish(db, key, fingerprint, result, _etag(result.model_dump(mode="json")))


@router.post("/devices/confirm", response_model=BulkResult,
             responses=MUTATION_RESPONSES, openapi_extra=MUTATION_OPENAPI)
async def confirm_devices(
    body: BulkConfirmation, db: DB, p: Admin, request: Request, headers: MutationHeaders,
) -> JSONResponse:
    """Bevestig de uitdrukkelijk geselecteerde apparaten in één transactie."""
    key, fingerprint, replay = await _prepare(db, p, request, headers, body)
    if replay is not None:
        return replay
    _, before, _ = await _gate_preview(db)
    _precondition(headers, before)
    selection = [await _find_device(db, device_id) for device_id in body.device_ids]
    for device, home, room in selection:
        review = await _review(db, device, home, room)
        _check_confirmation(review, device, home, bulk=True)
    for device, _, _ in selection:
        await record_test_confirmation(db, device, actor=p.actor, username=p.user.username, note=body.note)
    result = BulkResult(items=await _views(db, [await _review(db, d, h, room) for d, h, room in selection]))
    _, after, _ = await _gate_preview(db)
    return await _finish(db, key, fingerprint, result, after)


@router.post("/enable", response_model=GateView, responses=MUTATION_RESPONSES, openapi_extra=MUTATION_OPENAPI)
async def enable_commissioning(
    body: ExplicitConfirmation, db: DB, p: Admin, request: Request, headers: MutationHeaders,
) -> JSONResponse:
    """Schakel de gate in na bevestiging van het actuele installatiebrede gevolg."""
    key, fingerprint, replay = await _prepare(db, p, request, headers, body)
    if replay is not None:
        return replay
    view, etag, devices = await _gate_preview(db)
    _precondition(headers, etag)
    if not view.gate.known:
        raise contract_error(
            409, "conflict", "De gate is uit voorzorg actief. Controleer de configuratie via de herstelprocedure.",
            reason="unknown_gate_state",
        )
    if not view.gate.enabled:
        state = await read_gate(db)
        del state["known"]
        state.update(enabled=True, enabled_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"), enabled_by=p.actor)
        await set_setting(db, KEY, state)
        await audit(db, p.actor, "commissioning_gate_enabled", KEY, affected_count=view.affected_count)
        view.gate = Gate(**state, known=True)
        view.task_required = False
        etag = _gate_etag(view, devices)
    return await _finish(db, key, fingerprint, view, etag)
