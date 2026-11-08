"""The one way to request a device command.

``queue_command`` checks the safety policy and puts a command on the queue the worker
executes. ``request_zone_command`` is the full climate-zone command path (unit checks,
outdoor-unit conflicts, step rounding, policy, control/override state, zone log, audit,
realtime update). The API uses it today; climate and button code should route through
it as well, so every climate command passes the same checks.

Neither function commits: the caller owns the transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from . import climate as cl
from . import events, views
from .models import ClimateZone, CommandRecord, Device, Home, ZoneLog
from .policy import DeviceFacts, PolicyDenied, check_command
from .store import audit, get_setting, roles_of


class CommandRejected(Exception):
    """A command request refused before queueing. Maps 1:1 onto an API error response."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


async def queue_command(
    db: AsyncSession,
    home_id: uuid.UUID,
    device: Device,
    action: str,
    params: dict,
    source: str,
    actor: str,
    zone_id: uuid.UUID | None = None,
) -> CommandRecord:
    """Policy-check and queue one device command. Raises PolicyDenied."""
    roles, protected = await roles_of(db, device.id)
    check_command(
        DeviceFacts(device.kind, roles, protected, bool(await get_setting(db, "safe_mode", False))),
        action,
        params,
    )
    cmd = CommandRecord(
        home_id=home_id,
        device_id=device.id,
        zone_id=zone_id,
        action=action,
        params=params,
        source=source,
        actor=actor,
    )
    db.add(cmd)
    await db.flush()
    await events.publish(db, "command.queued", home_id, {"id": cmd.id})
    return cmd


async def request_zone_command(
    db: AsyncSession,
    zone: ClimateZone,
    home: Home,
    *,
    mode: str | None = None,
    target_c: float | None = None,
    fan: str | None = None,
    override_minutes: int | None = None,
    source: str,
    actor: str,
    log_name: str | None = None,
) -> CommandRecord:
    """Validate and queue a set_hvac command for a zone and update its control state.

    ``mode="off"`` switches the zone off until resumed; anything else is a manual override
    for ``override_minutes`` (default from the zone policy). ``actor`` goes to the command
    record and the audit log; ``log_name`` (default: ``actor``) prefixes the zone-log line.
    Raises CommandRejected; does not commit.
    """
    z = zone
    if not z.unit_device_id:
        raise CommandRejected(409, "unsupported", "Aan deze zone is nog geen airco-unit gekoppeld.")
    unit = await db.get(Device, z.unit_device_id)
    assert unit is not None
    if not unit.online:
        raise CommandRejected(503, "device_offline", "Airco niet bereikbaar. Gebruik de afstandsbediening.")
    caps = views.caps_of(unit)
    err = cl.validate_request(caps, await views.other_zone_modes(db, z), mode, target_c)
    if err:
        raise CommandRejected(409, "policy_denied", err)
    params: dict = {}
    if mode is not None:
        params["mode"] = mode
    if target_c is not None:
        step = caps.step_c
        params["target_c"] = round(round(target_c / step) * step, 2)
    if fan is not None:
        params["fan"] = fan
    if not params:
        raise CommandRejected(422, "validation_error", "Geen wijziging opgegeven.")
    try:
        cmd = await queue_command(db, home.id, unit, "set_hvac", params, source, actor, z.id)
    except PolicyDenied as e:
        raise CommandRejected(409, "policy_denied", e.message) from e
    now = datetime.now(UTC)
    pol = cl.policy_with_defaults(z.policy)
    if mode == "off":
        z.control, z.override_until = "off", None  # deliberate off: stays off until resume
    else:
        z.control = "manual"
        z.override_until = now + timedelta(minutes=override_minutes or pol["override_default_minutes"])
    z.override_source = "app" if source == "user" else source
    z.last_command_at = now
    z.last_commanded = {
        **(z.last_commanded or {}),
        **{k: v for k, v in params.items() if k in ("mode", "target_c")},
    }
    db.add(
        ZoneLog(
            zone_id=z.id,
            kind="user",
            message=f"{log_name or actor}: " + ", ".join(f"{k}={v}" for k, v in params.items()),
        )
    )
    await audit(db, actor, "zone_command", str(z.id), **params)
    await events.publish(db, "zone.updated", home.id, await views.zone_view(db, z, home))
    return cmd
