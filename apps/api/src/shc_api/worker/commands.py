"""Wachtrij naar adapters met hercontrole bij claimen én vlak voor uitvoering.

Eerst claimen en ``sending`` vastleggen. Toets daarna de actuele feiten en boek
het resultaat onder dezelfde woninglock als de adapteraanroep. De gedeelde
installatielock houdt herstel en wijzigingen aan het beleid buiten die transactie.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from .. import climate as cl
from .. import events
from ..commanding import (
    check_active_target,
    check_scene_group_policy,
    check_zigbee_bridge,
    command_still_wanted,
    lock_outdoor_group,
    publish_zone,
    zone_command_confirmed,
    zone_command_failed,
)
from ..commissioning import command_facts, lock_commissioning_shared, lock_home_commands
from ..db import sessionmaker
from ..logfilter import fouttekst
from ..models import ClimateZone, CommandRecord, Device, Home, ZoneLog
from ..policy import PolicyDenied, check_command
from ..store import get_setting
from ..views.common import caps_of, other_zone_modes
from ..views.lights import BRIDGE_ERROR_PREFIX

log = logging.getLogger("shc.worker")

MAX_AGE_FIRST = timedelta(minutes=2)  # a command not started within this is not executed any more
MAX_AGE_RETRY = timedelta(minutes=5)
SENDING_STUCK = timedelta(minutes=2)
ANY_RESTORE = object()


class CommandsMixin:
    # Provided by core.Worker: _adapter_for(device) -> adapter | None.

    async def process_commands(self, home_ids: set[uuid.UUID] | None = None) -> None:
        """Run every queued command that is due. ``home_ids`` limits the run (tests, diagnostics)."""
        restore_marker = await self._recover_interrupted(home_ids)
        while True:
            claimed = await self._claim_next(home_ids, restore_marker=restore_marker)
            if claimed is None:
                return
            if claimed is False:
                continue
            cmd_id, home_id, attempts, claimed_at = claimed
            async with sessionmaker()() as db:
                await lock_home_commands(db, home_id)
                if await get_setting(db, "restored_from") != restore_marker:
                    return
                cmd = await db.get(CommandRecord, cmd_id, with_for_update=True)
                if cmd is None or (cmd.status, cmd.attempts, cmd.updated_at) != ("sending", attempts, claimed_at):
                    continue
                dev = await db.get(Device, cmd.device_id)
                zone = await self._zone_of(db, cmd, dev)
                retry = cmd.attempts > 1
                verdict = await self._precheck(db, cmd, dev, zone, datetime.now(UTC), retry)
                if verdict is None:
                    # De controles kunnen wachten op vergrendelingen. Lees de klok opnieuw vóór verzending.
                    verdict = _ttl_verdict(cmd, datetime.now(UTC), retry)
                if verdict is not None:
                    cmd.status, cmd.detail = verdict
                    await self._settle(db, cmd, dev, zone, datetime.now(UTC), allow_retry=False)
                else:
                    status, detail, state = await self._send_command(db, cmd, dev)
                    await self._record_result(db, cmd, dev, zone, status, detail, state)
                await db.commit()

    async def _send_command(self, db, cmd: CommandRecord, dev: Device):
        try:
            res = await self._execute_command(db, cmd, dev)
        except PolicyDenied as exc:
            return "denied", exc.message, None
        except Exception as exc:  # time-out, adapterfout, niet ondersteund
            return "failed", fouttekst(exc)[:500] or type(exc).__name__, None
        status = "confirmed" if res.confirmed else "failed"
        if not res.confirmed and res.ok and not device_reported_error(res):
            status = "sent"
        return status, res.detail or "", res.state

    async def _execute_command(self, db, cmd: CommandRecord, dev: Device):
        from shc_integrations.base import Command
        from shc_integrations.hue import HueAdapter

        adapter = self._adapter_for(dev)
        if adapter is None:
            raise RuntimeError("Geen actieve integratie voor dit apparaat")
        action = cmd.action
        params = _translate(dev, action, dict(cmd.params))
        command = Command(dev.stable_identity, action, params)
        if isinstance(adapter, HueAdapter) and action in {"set_group", "recall_scene"}:
            async def check_targets(group: str, members: list[str]) -> None:
                await check_scene_group_policy(
                    db, dev, action, params, safe_mode=True, actual_hue_targets=(group, members),
                )

            return await adapter.execute(command, check_targets=check_targets)
        return await adapter.execute(command)

    async def _recover_interrupted(self, home_ids):
        """Commands left in 'sending' by a crash/restart: their outcome is unknown, mark them failed."""
        async with sessionmaker()() as db:
            await lock_commissioning_shared(db)
            marker = await get_setting(db, "restored_from")
            q = (
                update(CommandRecord)
                .where(
                    CommandRecord.status == "sending",
                    CommandRecord.updated_at < datetime.now(UTC) - SENDING_STUCK,
                )
                .values(status="failed", detail="Onderbroken (worker herstart); uitkomst onbekend")
            )
            if home_ids:
                q = q.where(CommandRecord.home_id.in_(home_ids))
            await db.execute(q)
            await db.commit()
            return marker

    async def _claim_next(self, home_ids, *, restore_marker=ANY_RESTORE):
        """Kies de oudste beschikbare woning en claim onder haar vergrendeling."""
        now = datetime.now(UTC)
        async with sessionmaker()() as db:
            await lock_commissioning_shared(db)
            if restore_marker is not ANY_RESTORE and await get_setting(db, "restored_from") != restore_marker:
                return None
            due = [CommandRecord.status == "queued", CommandRecord.updated_at <= now]
            if home_ids:
                due.append(CommandRecord.home_id.in_(home_ids))
            homes = await db.scalars(select(CommandRecord.home_id).where(*due).group_by(CommandRecord.home_id)
                                     .order_by(func.min(CommandRecord.created_at)))
            for home_id in homes:
                if await lock_home_commands(db, home_id, skip_busy=True):
                    break
            else:
                return None
            cmd = await db.scalar(select(CommandRecord).where(*due, CommandRecord.home_id == home_id)
                                  .order_by(CommandRecord.created_at).limit(1).with_for_update(skip_locked=True))
            if cmd is None:
                return False
            dev = await db.get(Device, cmd.device_id)
            retry = cmd.attempts > 0
            cmd.attempts += 1
            cmd.updated_at = now
            zone = await self._zone_of(db, cmd, dev)
            verdict = await self._precheck(db, cmd, dev, zone, now, retry)
            if verdict is not None:
                cmd.status, cmd.detail = verdict
                await self._settle(db, cmd, dev, zone, now, allow_retry=False)
                await db.commit()
                return False
            cmd.status = "sending"
            await db.commit()
            return cmd.id, home_id, cmd.attempts, now

    async def _zone_of(self, db, cmd: CommandRecord, dev: Device | None) -> ClimateZone | None:
        if cmd.action != "set_hvac" or dev is None:
            return None
        if cmd.zone_id:
            zone = await db.get(ClimateZone, cmd.zone_id)
            return zone if zone is not None and zone.home_id == cmd.home_id else None
        return (await db.scalars(select(ClimateZone).where(
            ClimateZone.unit_device_id == dev.id, ClimateZone.home_id == cmd.home_id,
        ))).first()

    async def _precheck(
        self, db, cmd: CommandRecord, dev: Device | None, zone: ClimateZone | None, now: datetime, retry: bool
    ) -> tuple[str, str] | None:
        """(status, detail) when the command must not run, else None. Takes the outdoor-unit lock for climate."""
        if dev is None:
            return "failed", "Apparaat bestaat niet meer"
        if dev.home_id != cmd.home_id:
            return "denied", "Dit apparaat hoort niet bij de woning van deze bediening."
        try:
            if dev.provider == "hue" and "id" in cmd.params and cmd.params["id"] != dev.stable_identity:
                raise PolicyDenied("Het opgeslagen Hue-doel wijkt af van het getoetste apparaat.")
            if cmd.action == "permit_join":
                await check_zigbee_bridge(db, cmd.home_id, dev, cmd.params)
            else:
                await check_scene_group_policy(db, dev, cmd.action, cmd.params, safe_mode=True)
                check_command(
                    await command_facts(db, dev, safe_mode=True),
                    cmd.action,
                    cmd.params,
                )
                await check_active_target(db, cmd.home_id, dev, climate=cmd.action == "set_hvac")
        except PolicyDenied as e:
            return "denied", e.message
        if (verdict := _ttl_verdict(cmd, now, retry)) is not None:
            return verdict
        if zone is None:
            return None
        await lock_outdoor_group(db, zone)
        await db.refresh(zone)
        superseded = zone.pending_command_id not in (None, cmd.id) and cmd.zone_id == zone.id
        if cmd.source == "engine" and not command_still_wanted(zone, cmd):
            return "cancelled", "Vervallen: de zone staat inmiddels op handmatig of uit."
        if superseded and (cmd.source == "engine" or retry):
            return "cancelled", "Vervallen: er is inmiddels een nieuwer commando voor deze zone."
        mode = cmd.params.get("mode")
        if mode not in (None, "off", "fan"):
            others = await other_zone_modes(db, zone, now, exclude_id=cmd.id, created_before=cmd.created_at)
            for b in cl.blocked_modes(caps_of(dev), others):
                if b["mode"] == mode:
                    return "denied", b["reason"]
        return None

    async def _record_result(
        self, db, cmd: CommandRecord, dev: Device, zone: ClimateZone | None, status: str, detail: str, state,
    ) -> None:
        now = datetime.now(UTC)
        if state and dev.provider == "gree":
            mode = "off" if state.get("power") is False else state.get("mode")
            dev.state = {**(dev.state or {}), **{k: v for k, v in state.items() if k != "raw"}, "mode": mode}
            dev.last_seen, dev.online = now, True
        cmd.status, cmd.detail, cmd.updated_at = status, detail, now
        await self._settle(db, cmd, dev, zone, now)

    async def _settle(
        self, db, cmd: CommandRecord, dev: Device | None, zone: ClimateZone | None, now, allow_retry: bool = True
    ) -> None:
        """Zone bookkeeping and realtime events for a command that reached a status."""
        if zone is not None and cmd.zone_id == zone.id:
            if cmd.status == "confirmed" and dev is not None:
                await zone_command_confirmed(db, zone, cmd, dev, now)
            elif cmd.status == "failed":
                await zone_command_failed(db, zone, cmd, now, retry=allow_retry)
            elif cmd.status in ("denied", "cancelled"):
                if zone.pending_command_id == cmd.id:
                    zone.pending_command_id = None
                    zone.pending_since = now
                kind = "warn" if cmd.status == "denied" else "engine"
                db.add(ZoneLog(zone_id=zone.id, kind=kind, message=f"Commando niet uitgevoerd: {cmd.detail}"))
            elif cmd.status == "sent":
                # The unit did not show the requested state yet. Keep it pending: the climate loop
                # confirms it on a later readback or treats it as failed after a timeout.
                db.add(ZoneLog(zone_id=zone.id, kind="warn", message=f"Verzonden, niet bevestigd: {cmd.detail}"))
            home = await db.get(Home, cmd.home_id)
            if home is not None:
                await publish_zone(db, zone, home)
        if cmd.status != "queued":  # a retry already published its own update
            await events.publish(
                db,
                "command.updated",
                cmd.home_id,
                {"id": cmd.id, "status": cmd.status, "detail": cmd.detail, "attempts": cmd.attempts},
            )


def _ttl_verdict(cmd: CommandRecord, now: datetime, retry: bool) -> tuple[str, str] | None:
    if now - cmd.created_at > (MAX_AGE_RETRY if retry else MAX_AGE_FIRST):
        return "failed", "Commando te oud; niet meer uitgevoerd"
    return None


def device_reported_error(res) -> bool:
    """True when an adapter result says the device or hub reported an error for the command.

    ``CommandResult.rejected`` is the contract flag; the "Hue Bridge meldt" prefix is kept as a
    fallback for results from adapters that predate the flag."""
    if getattr(res, "rejected", False):
        return True
    return bool(res.ok) and (res.detail or "").startswith(BRIDGE_ERROR_PREFIX)


def _translate(dev: Device, action: str, params: dict) -> dict:
    """Provider-specific command shape. Gree: power on only when a mode is chosen explicitly;
    a temperature/fan change alone never switches the unit on (verbeterlijst 13)."""
    if dev.provider == "gree" and action == "set_hvac":
        mode = params.get("mode")
        if mode == "off":
            return {"power": False}
        if mode is not None:
            return {"power": True, **params}
        return dict(params)
    return params
