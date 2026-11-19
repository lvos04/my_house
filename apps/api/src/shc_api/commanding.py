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
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from . import climate as cl
from . import events
from .commissioning import command_facts, lock_home_commands, read_gate
from .models import (
    AuditLog,
    ClimateZone,
    ComfortProfile,
    CommandRecord,
    Device,
    Experiment,
    Home,
    Integration,
    ScheduleBlock,
    ZoneLog,
)
from .policy import DeviceNotTested, PolicyDenied, check_command
from .store import audit, raise_alert, resolve_alert
from .views.common import caps_of, effective_mode, observed_baseline, other_zone_modes
from .views.lights import LIGHT_ACTIONS

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
    await lock_home_commands(db, zone.home_id)
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": outdoor_lock_key(zone)})


async def check_scene_group_policy(
    db: AsyncSession, device: Device, action: str, params: dict, *,
    protection_only: bool = False, safe_mode: bool = False,
    preview_enabled: bool = False,
    actual_hue_targets: tuple[str, list[str]] | None = None,
) -> None:
    """Toets indirecte doelen; bescherming gaat vóór iedere teststatus.

    De aanroeper toetst daarna het hoofddoel met dezelfde apparaatpolicy.
    Ontbrekende ledengegevens geven bij gate aan nooit een vrijstelling.
    """
    await lock_home_commands(db, device.home_id)
    await db.refresh(device)
    if device.kind not in {"scene", "light_group"}:
        return
    targets = [device]
    group = device if device.kind == "light_group" else None
    if device.kind == "scene":
        scene_state = device.state if isinstance(device.state, dict) else {}
        try:
            group_id = uuid.UUID(str(scene_state.get("group_device_id")))
        except (ValueError, TypeError):
            group_id = None
        group = await db.get(Device, group_id, populate_existing=True) if group_id else None
        if group is not None and group.home_id == device.home_id:
            await db.refresh(group)
            targets.append(group)
        else:
            group = None
    if actual_hue_targets is not None:
        actual_group, _ = actual_hue_targets
        if group is not None and group.stable_identity != actual_group:
            raise PolicyDenied("De Hue-scène hoort inmiddels bij een andere groep. Lees de apparaten opnieuw uit.")
        if group is None:
            group = await db.scalar(select(Device).where(
                Device.home_id == device.home_id, Device.provider == device.provider,
                Device.stable_identity == actual_group,
            ).execution_options(populate_existing=True))
            if group is None:
                raise PolicyDenied("De actuele Hue-groep is niet bekend. Lees de apparaten opnieuw uit.")
            targets.append(group)
    unknown_members = group is None
    if group is not None:
        unknown_members = (group.kind != "light_group" or group.lifecycle != "active"
                           or group.provider != device.provider or group.integration_id != device.integration_id)
        state = group.state if isinstance(group.state, dict) else {}
        if actual_hue_targets is not None:
            state = {"member_identities": actual_hue_targets[1], "members_complete": True}
        identities = state.get("member_identities")
        valid_ids = {i for i in identities if isinstance(i, str) and i} if isinstance(identities, list) else set()
        unknown_members |= (not isinstance(identities, list)
                            or any(not isinstance(i, str) or not i for i in identities)
                            or state.get("members_complete") is not True)
        # Lees ook ongeldige/gearchiveerde leden: hun bescherming houdt voorrang.
        members = list((await db.execute(select(Device).where(
            Device.home_id == group.home_id, Device.provider == group.provider,
            Device.stable_identity.in_(valid_ids),
        ).execution_options(populate_existing=True))).scalars())
        targets.extend(members)
        unknown_members |= ({m.stable_identity for m in members} != valid_ids or any(
            m.kind != "light" or m.lifecycle != "active" or m.integration_id != group.integration_id
            for m in members
        ))
        if actual_hue_targets is not None and unknown_members:
            # Onbekende apparaten kunnen alleen bij uitgeschakelde gate meedoen,
            # maar een bekend lid uit een andere koppeling is een tegenstrijdigheid.
            if (group.kind != "light_group" or group.lifecycle != "active"
                    or group.integration_id != device.integration_id or any(
                        m.kind != "light" or m.lifecycle != "active" or m.integration_id != group.integration_id
                        for m in members
                    )):
                raise PolicyDenied("De actuele Hue-groep komt niet overeen met de geregistreerde apparaten.")
    facts = [await command_facts(db, target, safe_mode=safe_mode) for target in targets]
    if preview_enabled:
        facts = [replace(item, commissioning_gate=True) for item in facts]
    for item in facts:
        check_command(replace(item, commissioning_gate=False), action, params)
    if protection_only:
        return
    for item in facts[1:]:
        check_command(item, action, params)
    if unknown_members and facts[0].commissioning_gate:
        raise DeviceNotTested("De groepsleden zijn nog niet volledig bekend. Lees de apparaten opnieuw uit en "
                             "laat een beheerder alle doelen onder Apparaten testen en bevestigen.")


async def check_button_targets(
    db: AsyncSession, home_id: uuid.UUID, steps: Iterable[dict], *, safe_mode: bool = False,
) -> None:
    """Toets de bekende doelen van de hele koppeling vóór de eerste stap effect heeft."""
    await lock_home_commands(db, home_id)
    if not (await read_gate(db))["enabled"]:
        return
    targets: list[tuple[Device, str]] = []
    for step in steps:
        zone_step = step["type"] == "zone"
        try:
            ref = uuid.UUID(str(step.get("zone_id" if zone_step else "device_id")))
        except ValueError:
            continue  # De bestaande stapvalidatie meldt ontbrekende of ongeldige verwijzingen.
        if zone_step:
            zone = await db.get(ClimateZone, ref, populate_existing=True)
            if zone is None or zone.home_id != home_id or zone.unit_device_id is None:
                continue
            ref = zone.unit_device_id
        device = await db.get(Device, ref, populate_existing=True)
        if device is not None and device.home_id == home_id:
            action = "set_hvac" if zone_step else (LIGHT_ACTIONS.get(device.kind) or ("set_light",))[0]
            targets.append((device, action))

    for protection_only in (True, False):
        for device, action in targets:
            await check_scene_group_policy(db, device, action, {}, protection_only=protection_only,
                                           safe_mode=safe_mode)
            facts = await command_facts(db, device, safe_mode=safe_mode)
            check_command(replace(facts, commissioning_gate=False) if protection_only else facts, action, {})


async def check_zone_configuration(db: AsyncSession, *zones: ClimateZone) -> None:
    """Gebruik de actuele policy voor alle zones die een wijziging kan activeren."""
    for home_id in sorted({zone.home_id for zone in zones}):
        await lock_home_commands(db, home_id)
    targets = []
    for zone in zones:
        await db.refresh(zone)
        unit = await db.get(Device, zone.unit_device_id) if zone.unit_device_id else None
        if unit is not None:
            targets.append(await command_facts(db, unit))
    for protection_only in (True, False):
        for facts in targets:
            check_command(replace(facts, commissioning_gate=False) if protection_only else facts, "set_hvac", {})


async def legacy_reinstalled_unit(db: AsyncSession, device: Device) -> bool:
    """Herken een terugplaatsing uit een versie die de levensloop niet terugzette."""
    if device.kind != "ac_unit" or device.lifecycle != "replaced":
        return False
    linked = await db.scalar(select(ClimateZone.id).where(
        ClimateZone.home_id == device.home_id, ClimateZone.unit_device_id == device.id,
    ).limit(1))
    if linked is None:
        return False
    device_id = str(device.id)
    latest = await db.scalar(select(AuditLog.detail).where(
        AuditLog.action == "device_replaced",
        (AuditLog.detail["old"].astext == device_id) | (AuditLog.detail["new"].astext == device_id),
    ).order_by(AuditLog.id.desc()).limit(1))
    return latest is not None and latest.get("new") == device_id


async def check_active_target(db: AsyncSession, home_id: uuid.UUID, device: Device, *, climate: bool = False) -> None:
    home = await db.get(Home, home_id, populate_existing=True)
    if home is None or home.active_to is not None or device.home_id != home_id:
        raise PolicyDenied("Dit apparaat hoort niet bij een actieve woning voor deze bediening.")
    if device.lifecycle == "active":
        return
    if climate and not (await read_gate(db))["enabled"] and await legacy_reinstalled_unit(db, device):
        return
    raise PolicyDenied("Dit apparaat is gearchiveerd of vervangen.")


async def check_zigbee_bridge(db: AsyncSession, home_id: uuid.UUID, bridge: Device, params: dict) -> Integration:
    await check_active_target(db, home_id, bridge)
    check_command(await command_facts(db, bridge, safe_mode=True), "permit_join", params)
    integration = (
        await db.get(Integration, bridge.integration_id, populate_existing=True) if bridge.integration_id else None
    )
    if (bridge.provider != "zigbee" or integration is None or integration.provider != "zigbee"
            or integration.home_id != home_id or not integration.enabled
            or ("integration_id" in params and params["integration_id"] != str(integration.id))):
        raise PolicyDenied("De actieve Zigbee-koppeling ontbreekt of is gewijzigd.")
    return integration


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
    if action == "permit_join":
        integration = await check_zigbee_bridge(db, home_id, device, params)
        params = {**params, "integration_id": str(integration.id)}
    else:
        await check_scene_group_policy(db, device, action, params, safe_mode=True)
        check_command(
            await command_facts(db, device, safe_mode=True),
            action,
            params,
        )
        await check_active_target(db, home_id, device, climate=action == "set_hvac")
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


async def queue_zone_setpoint(
    db: AsyncSession, zone: ClimateZone, unit: Device, target_c: float, *, source: str, actor: str,
) -> CommandRecord:
    """Volg de bestaande opdrachtadministratie bij een wijziging zonder nieuwe override."""
    await lock_outdoor_group(db, zone)
    cmd = await queue_command(db, zone.home_id, unit, "set_hvac", {"target_c": target_c}, source, actor, zone.id)
    _mark_pending(zone, cmd, cmd.created_at)
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
    caps = caps_of(unit)
    now = datetime.now(UTC)
    # Safety policy first (safe mode, protected roles): it outranks every other answer.
    try:
        check_command(
            await command_facts(db, unit, safe_mode=True),
            "set_hvac",
            {"mode": mode, "target_c": target_c, "fan": fan},
        )
    except PolicyDenied as e:
        raise CommandRejected(409, e.code, e.message) from e
    if not unit.online:
        raise CommandRejected(503, "device_offline", "Airco niet bereikbaar. Gebruik de afstandsbediening.")
    if mode is None and target_c is None and fan is None:
        raise CommandRejected(422, "validation_error", "Geen wijziging opgegeven.")
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
        raise CommandRejected(409, e.code, e.message) from e
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


# Policy keys a profile overwrites (apply_profile); a snapshot keeps their earlier values.
PROFILE_POLICY_KEYS = ("band_c", "energy_priority")


async def zone_snapshot(db: AsyncSession, zone: ClimateZone) -> dict:
    """What apply_profile is about to replace, so it can be put back later (experiments, item 45):
    profile, schedule blocks, band/energy priority and the desired mode/target. JSON-safe."""
    blocks = (
        await db.execute(select(ScheduleBlock).where(ScheduleBlock.zone_id == zone.id).order_by(ScheduleBlock.start))
    ).scalars()
    policy = zone.policy or {}
    return {
        "profile_id": str(zone.profile_id) if zone.profile_id else None,
        "profile": zone.profile,
        "policy": {k: policy[k] for k in PROFILE_POLICY_KEYS if k in policy},
        "desired_mode": zone.desired_mode,
        "desired_target_c": zone.desired_target_c,
        "schedule": [
            {"days": list(b.days or []), "start": b.start, "target_c": b.target_c, "mode": b.mode, "label": b.label}
            for b in blocks
        ],
    }


async def restore_zone_snapshot(
    db: AsyncSession, zone: ClimateZone, snap: dict, actor: str, log_name: str | None = None
) -> None:
    """Put back what :func:`zone_snapshot` saved: schedule, profile, band/energy priority and the
    desired mode/target. Like apply_profile, a running manual override stays. Does not commit."""
    await db.execute(delete(ScheduleBlock).where(ScheduleBlock.zone_id == zone.id))
    for b in snap.get("schedule") or []:
        db.add(
            ScheduleBlock(
                zone_id=zone.id,
                days=list(b.get("days") or []),
                start=b["start"],
                target_c=float(b["target_c"]),
                mode=b.get("mode"),
                label=b.get("label") or "",
            )
        )
    pid = snap.get("profile_id")
    profile = await db.get(ComfortProfile, uuid.UUID(pid)) if pid else None
    zone.profile_id = profile.id if profile is not None else None
    zone.profile = snap.get("profile")
    policy = {k: v for k, v in (zone.policy or {}).items() if k not in PROFILE_POLICY_KEYS}
    zone.policy = {**policy, **(snap.get("policy") or {})}
    if snap.get("desired_mode"):
        zone.desired_mode = snap["desired_mode"]
    zone.desired_target_c = snap.get("desired_target_c")
    name = snap.get("profile") or "zonder profiel"
    db.add(
        ZoneLog(
            zone_id=zone.id,
            kind="config",
            message=f"{log_name or actor}: profiel en schema van vóór het experiment teruggezet ({name})",
        )
    )
    await audit(db, actor, "profile_restored", str(zone.id), profile=pid, name=snap.get("profile"))


# Experiment.result keys for auto-apply experiments: the zone as it was before the first variant,
# and the profile the experiment put on the zone last (None while nothing of it is active).
EXPERIMENT_BEFORE = "zone_before"
EXPERIMENT_APPLIED = "applied_profile_id"


async def experiment_apply_variant(
    db: AsyncSession, e: Experiment, zone: ClimateZone, profile: ComfortProfile, actor: str
) -> None:
    """Put a variant's profile on the zone (caller holds the outdoor-group lock and refreshed the
    zone). Before the first variant the zone is saved, so it can be put back afterwards."""
    res = dict(e.result or {})
    if not res.get(EXPERIMENT_BEFORE):
        res[EXPERIMENT_BEFORE] = await zone_snapshot(db, zone)
    await apply_profile(db, zone, profile, actor, log_name=f"Experiment '{e.name}'")
    res[EXPERIMENT_APPLIED] = str(profile.id)
    e.result = res


async def experiment_zone(db: AsyncSession, experiment: Experiment) -> ClimateZone | None:
    """Lees ook de zoneverwijzing in varianten uit oudere experimenten."""
    zone_id = experiment.zone_id
    if zone_id is None and experiment.variants:
        try:
            zone_id = uuid.UUID(str(experiment.variants[0].get("zone_id")))
        except ValueError:
            return None
    zone = await db.get(ClimateZone, zone_id) if zone_id else None
    return zone if zone is not None and zone.home_id == experiment.home_id else None


async def experiment_restore_zone(db: AsyncSession, e: Experiment, zone: ClimateZone, actor: str) -> str:
    """Switch the experiment's profile off again (verbeterlijst 45): the zone gets back the profile
    and schedule it had before the first variant. Takes the outdoor-group lock; does not commit.

    Returns "restored", "none" (no variant profile is active) or "changed" (someone put another
    profile on the zone meanwhile: that choice stays, only the experiment lets go)."""
    res = dict(e.result or {})
    snap, applied = res.get(EXPERIMENT_BEFORE), res.get(EXPERIMENT_APPLIED)
    if not snap or not applied:
        return "none"
    await lock_outdoor_group(db, zone)
    await db.refresh(zone)
    restore = str(zone.profile_id) == applied
    if restore:
        await check_zone_configuration(db, zone)
    res[EXPERIMENT_APPLIED] = None
    e.result = res
    if not restore:
        db.add(
            ZoneLog(
                zone_id=zone.id,
                kind="config",
                message=f"Experiment '{e.name}': profiel niet teruggezet, de zone kreeg intussen een ander profiel",
            )
        )
        return "changed"
    await restore_zone_snapshot(db, zone, snap, actor, log_name=f"Experiment '{e.name}'")
    return "restored"


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
