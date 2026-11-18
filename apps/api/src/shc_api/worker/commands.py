"""Wachtrij naar adapters met hercontrole bij claimen én vlak voor uitvoering.

Eerst claimen en ``sending`` vastleggen. Daarna de installatiebrede vergrendeling
opnieuw nemen, actuele feiten en klimaatvoorwaarden lezen en toetsen. Die tweede
transactie houdt de vergrendeling tot de adapteraanroep klaar is: teststatus,
bescherming en inschakeling kunnen niet tijdens de uitvoering voorbijlopen.
Daarna volgen readback, zoneboekhouding, begrensde retries en ``command.updated``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from .. import climate as cl
from .. import events
from ..commanding import (
    check_active_target,
    check_scene_group_policy,
    command_still_wanted,
    lock_outdoor_group,
    publish_zone,
    zone_command_confirmed,
    zone_command_failed,
)
from ..commissioning import command_facts, lock_commissioning
from ..db import sessionmaker
from ..logfilter import fouttekst
from ..models import ClimateZone, CommandRecord, Device, Home, ZoneLog
from ..policy import PolicyDenied, check_command
from ..views.common import caps_of, other_zone_modes
from ..views.lights import BRIDGE_ERROR_PREFIX

log = logging.getLogger("shc.worker")

MAX_AGE_FIRST = timedelta(minutes=2)  # a command not started within this is not executed any more
MAX_AGE_RETRY = timedelta(minutes=5)
SENDING_STUCK = timedelta(minutes=2)


class CommandsMixin:
    # Provided by core.Worker: _adapter_for(device) -> adapter | None.

    async def process_commands(self, home_ids: set[uuid.UUID] | None = None) -> None:
        """Run every queued command that is due. ``home_ids`` limits the run (tests, diagnostics)."""
        await self._recover_interrupted(home_ids)
        while True:
            claimed = await self._claim_next(home_ids)
            if claimed is None:
                return
            if claimed is False:
                continue
            cmd_id, dev = claimed
            ok, confirmed, detail, state, denied = False, False, "", None, False
            try:
                async with sessionmaker()() as db:
                    # Houd dezelfde transactielock ook tijdens de laatste controle en uitvoering.
                    # Inschakelen/binden kan dan niet tussen controle en adapteraanroep komen.
                    await lock_commissioning(db)
                    cmd = await db.get(CommandRecord, cmd_id)
                    dev = await db.get(Device, cmd.device_id)
                    zone = await self._zone_of(db, cmd, dev)
                    verdict = await self._precheck(db, cmd, dev, zone, datetime.now(UTC), cmd.attempts > 1)
                    if verdict is not None:
                        cmd.status, cmd.detail = verdict
                        await self._settle(db, cmd, dev, zone, datetime.now(UTC), allow_retry=False)
                        await db.commit()
                        continue
                    res = await self._execute_command(db, cmd, dev)
                    ok, confirmed, detail, state = res.ok, res.confirmed, res.detail, res.state
                if not confirmed and device_reported_error(res):
                    # Received but refused by the device/hub (e.g. Hue Bridge "errors"): store it as
                    # failed with the message, the same as the lights view shows it (item 27).
                    ok = False
            except PolicyDenied as e:
                detail, denied = e.message, True
            except Exception as e:  # adapter errors, timeouts, unsupported
                ok, confirmed, detail = False, False, fouttekst(e)[:500] or type(e).__name__
            await self._record_result(cmd_id, ok, confirmed, detail, state, denied=denied)

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

    async def _recover_interrupted(self, home_ids) -> None:
        """Commands left in 'sending' by a crash/restart: their outcome is unknown, mark them failed."""
        async with sessionmaker()() as db:
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

    async def _claim_next(self, home_ids):
        """Returns (command id, device) to execute, False when a command was settled without
        executing it, or None when the queue is empty."""
        now = datetime.now(UTC)
        async with sessionmaker()() as db:
            await lock_commissioning(db)
            q = (
                select(CommandRecord)
                .where(CommandRecord.status == "queued", CommandRecord.updated_at <= now)
                .order_by(CommandRecord.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if home_ids:
                q = q.where(CommandRecord.home_id.in_(home_ids))
            cmd = (await db.execute(q)).scalar_one_or_none()
            if cmd is None:
                return None
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
            return cmd.id, dev

    async def _zone_of(self, db, cmd: CommandRecord, dev: Device | None) -> ClimateZone | None:
        if cmd.action != "set_hvac" or dev is None:
            return None
        if cmd.zone_id:
            return await db.get(ClimateZone, cmd.zone_id)
        return (await db.execute(select(ClimateZone).where(ClimateZone.unit_device_id == dev.id))).scalars().first()

    async def _precheck(
        self, db, cmd: CommandRecord, dev: Device | None, zone: ClimateZone | None, now: datetime, retry: bool
    ) -> tuple[str, str] | None:
        """(status, detail) when the command must not run, else None. Takes the outdoor-unit lock for climate."""
        if dev is None:
            return "failed", "Apparaat bestaat niet meer"
        try:
            if dev.provider == "hue" and "id" in cmd.params and cmd.params["id"] != dev.stable_identity:
                raise PolicyDenied("Het opgeslagen Hue-doel wijkt af van het getoetste apparaat.")
            await check_scene_group_policy(db, dev, cmd.action, cmd.params, safe_mode=True)
            check_command(
                await command_facts(db, dev, safe_mode=True),
                cmd.action,
                cmd.params,
            )
            await check_active_target(db, cmd.home_id, dev)
        except PolicyDenied as e:
            return "denied", e.message
        if now - cmd.created_at > (MAX_AGE_RETRY if retry else MAX_AGE_FIRST):
            return "failed", "Commando te oud; niet meer uitgevoerd"
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
        self, cmd_id: uuid.UUID, ok: bool, confirmed: bool, detail: str, state, *, denied: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        async with sessionmaker()() as db:
            await lock_commissioning(db)
            cmd = await db.get(CommandRecord, cmd_id)
            dev = await db.get(Device, cmd.device_id)
            # Device row first, outdoor-unit lock after: nothing that holds that lock waits for a device row.
            if state and dev is not None and dev.provider == "gree":
                mode = "off" if state.get("power") is False else state.get("mode")
                dev.state = {**(dev.state or {}), **{k: v for k, v in state.items() if k != "raw"}, "mode": mode}
                dev.last_seen, dev.online = now, True
                await db.flush()
            zone = await self._zone_of(db, cmd, dev)
            if zone is not None:
                await lock_outdoor_group(db, zone)
                await db.refresh(zone)
            cmd.status = "denied" if denied else ("confirmed" if confirmed else ("sent" if ok else "failed"))
            cmd.detail = detail or ""
            cmd.updated_at = now
            await self._settle(db, cmd, dev, zone, now)
            await db.commit()

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
