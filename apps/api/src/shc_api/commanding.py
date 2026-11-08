"""The one way to request a device command, and the climate-zone control state around it.

``queue_command`` checks the safety policy and puts a command on the queue the worker
executes. ``request_zone_command`` is the full climate-zone command path (unit checks,
"choose a mode first", outdoor-unit conflicts under a lock, step rounding, policy,
control/override state, zone log, audit, realtime update). The API and button actions use
it; the climate engine uses ``queue_engine_command``. Every climate command takes the same
per-outdoor-unit advisory lock (``lock_outdoor_group``) as the worker does right before
execution, so a conflicting command can never slip in between check and queue.

Command tracking on the zone (verbeterlijst 11):
- ``pending_command_id``/``pending_since``: the command in flight. ``last_commanded`` (the
  baseline for remote-control detection) is only updated after a confirmed readback.
- After a command completes, ``pending_since`` stays set (with ``pending_command_id`` empty)
  until the climate loop has seen one readback after it ("settling").
- A failed command is retried a limited number of times (same command id, so clients keep
  tracking it); after that an alert is raised. The zone's control mode never changes because
  of a failure.

None of the functions commit: the caller owns the transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from . import climate as cl
from . import events
from .models import ClimateZone, ComfortProfile, CommandRecord, Device, Home, ScheduleBlock, ZoneLog
from .policy import DeviceFacts, PolicyDenied, check_command
from .store import audit, get_setting, raise_alert, resolve_alert, roles_of
from .views.common import caps_of, effective_mode, observed_baseline, other_zone_modes

MAX_ATTEMPTS = 3  # first try + 2 retries
RETRY_DELAY = timedelta(seconds=15)
ENGINE_ACTOR = "klimaatregelaar"


class CommandRejected(Exception):
    """A command request refused before queueing. Maps 1:1 onto an API error response."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def outdoor_lock_key(zone: ClimateZone) -> str:
    """Lock name for everything that shares one outdoor unit (or just this zone without a group)."""
    if zone.outdoor_group:
        return f"shc-outdoor:{zone.home_id}:{zone.outdoor_group}"
    return f"shc-zone:{zone.id}"


async def lock_outdoor_group(db: AsyncSession, zone: ClimateZone) -> None:
    """Transaction-scoped advisory lock per outdoor unit; released on commit/rollback."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": outdoor_lock_key(zone)})


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
    now = datetime.now(UTC)
    cmd = CommandRecord(
        home_id=home_id,
        device_id=device.id,
        zone_id=zone_id,
        action=action,
        params=params,
        source=source,
        actor=actor,
        created_at=now,
        updated_at=now,
    )
    db.add(cmd)
    await db.flush()
    await events.publish(db, "command.queued", home_id, {"id": cmd.id})
    return cmd


def _mark_pending(zone: ClimateZone, cmd: CommandRecord, now: datetime) -> None:
    zone.pending_command_id = cmd.id
    zone.pending_since = now
    zone.mismatch_count = 0
    zone.last_command_at = now


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
    for ``override_minutes`` (default from the zone policy). A temperature or fan change
    without a mode while the unit is off is refused ("Kies eerst een modus"): it would
    otherwise start the unit in an old mode. ``actor`` goes to the command record and the
    audit log; ``log_name`` (default: ``actor``) prefixes the zone-log line.
    Raises CommandRejected; does not commit.
    """
    z = zone
    if not z.unit_device_id:
        raise CommandRejected(409, "unsupported", "Aan deze zone is nog geen airco-unit gekoppeld.")
    # Re-check under the outdoor-unit lock with fresh rows: no command can be queued in between.
    await lock_outdoor_group(db, z)
    await db.refresh(z)
    unit = await db.get(Device, z.unit_device_id)
    if unit is None:
        raise CommandRejected(409, "unsupported", "Aan deze zone is nog geen airco-unit gekoppeld.")
    await db.refresh(unit)
    if not unit.online:
        raise CommandRejected(503, "device_offline", "Airco niet bereikbaar. Gebruik de afstandsbediening.")
    if mode is None and target_c is None and fan is None:
        raise CommandRejected(422, "validation_error", "Geen wijziging opgegeven.")
    caps = caps_of(unit)
    now = datetime.now(UTC)
    # Safety policy first (safe mode, protected roles): it outranks every other answer.
    roles, protected = await roles_of(db, unit.id)
    try:
        check_command(
            DeviceFacts(unit.kind, roles, protected, bool(await get_setting(db, "safe_mode", False))),
            "set_hvac",
            {"mode": mode, "target_c": target_c, "fan": fan},
        )
    except PolicyDenied as e:
        raise CommandRejected(409, "policy_denied", e.message) from e
    current = await effective_mode(db, unit, now)
    if mode is None and current in (None, "off"):
        raise CommandRejected(
            409, "mode_required", "Kies eerst een modus: de airco staat uit en zou anders in een oude modus starten."
        )
    if fan is not None and caps.fan is False:
        raise CommandRejected(409, "unsupported", "Deze airco heeft geen instelbare ventilator.")
    others = await other_zone_modes(db, z, now)
    err = cl.validate_request(caps, others, mode, target_c)
    if err:
        raise CommandRejected(409, "policy_denied", err)
    params: dict = {}
    if mode is not None:
        params["mode"] = mode
    if target_c is not None:
        params["target_c"] = cl.round_to_step(target_c, caps.step_c)
    if fan is not None:
        params["fan"] = fan
    try:
        cmd = await queue_command(db, home.id, unit, "set_hvac", params, source, actor, z.id)
    except PolicyDenied as e:
        raise CommandRejected(409, "policy_denied", e.message) from e
    pol = cl.policy_with_defaults(z.policy)
    if mode == "off":
        z.control, z.override_until = "off", None  # deliberate off: stays off until resume
    else:
        z.control = "manual"
        z.override_until = now + timedelta(minutes=override_minutes or pol["override_default_minutes"])
    if mode not in (None, "off"):
        z.desired_mode = mode
    if target_c is not None:
        z.desired_target_c = params["target_c"]
    z.override_source = "app" if source == "user" else source
    _mark_pending(z, cmd, now)
    db.add(
        ZoneLog(
            zone_id=z.id,
            kind="user",
            message=f"{log_name or actor}: " + ", ".join(f"{k}={v}" for k, v in params.items()),
        )
    )
    await audit(db, actor, "zone_command", str(z.id), **params)
    await publish_zone(db, z, home)
    return cmd


async def queue_engine_command(
    db: AsyncSession,
    zone: ClimateZone,
    home: Home,
    unit: Device,
    params: dict,
    *,
    starting: bool = False,
    now: datetime | None = None,
) -> CommandRecord:
    """Queue a command decided by the climate engine. Raises PolicyDenied. Caller holds the group lock."""
    now = now or datetime.now(UTC)
    cmd = await queue_command(db, home.id, unit, "set_hvac", params, "engine", ENGINE_ACTOR, zone.id)
    _mark_pending(zone, cmd, now)
    if starting:
        zone.start_times = [
            t for t in (zone.start_times or []) if now - datetime.fromisoformat(t) < timedelta(hours=1)
        ] + [now.isoformat()]
    if params.get("mode") not in (None, "off"):
        zone.desired_mode = params["mode"]
    return cmd


async def resume_zone(db: AsyncSession, zone: ClimateZone, home: Home, actor: str, log_name: str | None = None) -> None:
    """Back to schedule: drop the manual override / deliberate off. Does not commit."""
    await lock_outdoor_group(db, zone)
    await db.refresh(zone)
    zone.control, zone.override_until, zone.override_source = "schedule", None, None
    zone.last_commanded = {}  # the engine adopts the unit's current state as new baseline
    zone.mismatch_count = 0
    db.add(ZoneLog(zone_id=zone.id, kind="user", message=f"{log_name or actor}: terug naar schema"))
    await audit(db, actor, "zone_resume", str(zone.id))
    await publish_zone(db, zone, home)


async def publish_zone(db: AsyncSession, zone: ClimateZone, home: Home) -> None:
    from .views.zones import zone_view

    await events.publish(db, "zone.updated", home.id, await zone_view(db, zone, home))


# --- command outcome bookkeeping (worker) ---------------------------------------------


def command_still_wanted(zone: ClimateZone, cmd: CommandRecord) -> bool:
    """An engine command only while the zone follows the schedule; a user/button command only
    while the zone is still under that manual control."""
    automatic = zone.control in ("schedule", "oem_fallback")
    return automatic if cmd.source == "engine" else not automatic


async def zone_command_confirmed(
    db: AsyncSession, zone: ClimateZone, cmd: CommandRecord, unit: Device, now: datetime
) -> None:
    """A readback confirmed the command: it becomes the new baseline."""
    zone.last_commanded = {**observed_baseline(unit), "at": now.isoformat()}
    zone.mismatch_count = 0
    if zone.pending_command_id == cmd.id:
        zone.pending_command_id = None
        zone.pending_since = now  # settle: skip detection until one readback after this
    await resolve_alert(db, f"command:{zone.id}")


async def zone_command_failed(
    db: AsyncSession, zone: ClimateZone, cmd: CommandRecord, now: datetime, *, retry: bool = True
) -> bool:
    """Handle a failed / unconfirmed zone command. Retries (same command) while attempts remain
    and the command is still wanted; otherwise gives up with an alert. Never changes the zone's
    control mode or baseline. Returns True when the command was queued again."""
    reason = cmd.detail or "onbekende fout"
    is_current = zone.pending_command_id == cmd.id
    if retry and is_current and cmd.attempts < MAX_ATTEMPTS and command_still_wanted(zone, cmd):
        cmd.status = "queued"
        cmd.updated_at = now + RETRY_DELAY
        cmd.detail = f"{reason} — opnieuw proberen (poging {cmd.attempts + 1} van {MAX_ATTEMPTS})"[:500]
        db.add(ZoneLog(zone_id=zone.id, kind="warn", message=f"Commando mislukt: {reason}. Opnieuw proberen."))
        await events.publish(
            db,
            "command.updated",
            cmd.home_id,
            {"id": cmd.id, "status": cmd.status, "detail": cmd.detail, "attempts": cmd.attempts, "retrying": True},
        )
        return True
    if is_current:
        zone.pending_command_id = None
        zone.pending_since = now  # settle; the baseline stays what the unit last confirmed
    if not command_still_wanted(zone, cmd):
        return False  # superseded by a newer choice: nothing to report
    db.add(
        ZoneLog(
            zone_id=zone.id,
            kind="error",
            message=f"Commando niet uitgevoerd: {reason}. De regelstand is niet aangepast.",
        )
    )
    await raise_alert(
        db,
        zone.home_id,
        f"command:{zone.id}",
        "error",
        f"Airco {zone.name} voert commando niet uit",
        f"{reason}. Na {cmd.attempts} pogingen opgegeven; de regelstand is niet aangepast. "
        "Controleer of de airco aan het netwerk hangt.",
        "climate",
    )
    return False


# --- comfort profiles ---------------------------------------------------------------


def profile_blocks(profile: ComfortProfile) -> list[dict]:
    """Schedule blocks a profile puts on a zone. Blocks without a mode inherit the profile's mode;
    a profile without a schedule becomes one constant block."""
    blocks = list(profile.schedule or [])
    if not blocks:
        blocks = [{"days": list(range(7)), "start": "00:00", "target_c": profile.target_c, "label": profile.name}]
    out = []
    for b in blocks:
        out.append(
            {
                "days": sorted(set(b["days"])),
                "start": b["start"],
                "target_c": float(b["target_c"]),
                "mode": b.get("mode") or profile.mode,
                "label": b.get("label") or profile.name,
            }
        )
    return out


async def apply_profile(
    db: AsyncSession, zone: ClimateZone, profile: ComfortProfile, actor: str, log_name: str | None = None
) -> None:
    """Put a comfort profile on a zone: replaces its schedule, sets band and energy priority.
    A running manual override or deliberate off stays as it is. Does not commit."""
    await db.execute(delete(ScheduleBlock).where(ScheduleBlock.zone_id == zone.id))
    for b in profile_blocks(profile):
        db.add(ScheduleBlock(zone_id=zone.id, **b))
    zone.profile_id = profile.id
    zone.profile = profile.name
    zone.policy = {**(zone.policy or {}), "band_c": profile.band_c, "energy_priority": profile.energy_priority}
    if profile.mode not in (None, "off"):
        zone.desired_mode = profile.mode
    zone.desired_target_c = profile.target_c
    db.add(ZoneLog(zone_id=zone.id, kind="config", message=f"{log_name or actor}: profiel '{profile.name}' toegepast"))
    await audit(db, actor, "profile_applied", str(zone.id), profile=str(profile.id), name=profile.name)


async def zones_using_profile(db: AsyncSession, profile_id: uuid.UUID) -> list[ClimateZone]:
    return list((await db.execute(select(ClimateZone).where(ClimateZone.profile_id == profile_id))).scalars())


async def count_pending(db: AsyncSession, device_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(CommandRecord)
            .where(CommandRecord.device_id == device_id, CommandRecord.status.in_(("queued", "sending")))
        )
    ).scalar_one()
