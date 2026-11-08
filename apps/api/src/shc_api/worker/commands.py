"""Command queue -> adapters. Policy is checked again right before execution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .. import events, views
from ..db import sessionmaker
from ..models import ClimateZone, CommandRecord, Device, Home, ZoneLog
from ..policy import DeviceFacts, PolicyDenied, check_command
from ..store import get_setting, roles_of


class CommandsMixin:
    # Provided by core.Worker: _adapter_for(device) -> adapter | None.

    async def process_commands(self) -> None:
        from shc_integrations.base import Command

        while True:
            async with sessionmaker()() as db:
                cmd = (
                    await db.execute(
                        select(CommandRecord)
                        .where(CommandRecord.status == "queued")
                        .order_by(CommandRecord.created_at)
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()
                if cmd is None:
                    return
                dev = await db.get(Device, cmd.device_id)
                cmd.attempts += 1
                cmd.updated_at = datetime.now(UTC)
                try:
                    roles, protected = await roles_of(db, dev.id)
                    check_command(
                        DeviceFacts(dev.kind, roles, protected, bool(await get_setting(db, "safe_mode", False))),
                        cmd.action,
                        cmd.params,
                    )
                    if datetime.now(UTC) - cmd.created_at > timedelta(minutes=2):
                        raise TimeoutError("Commando te oud; niet meer uitgevoerd")
                    adapter = self._adapter_for(dev)
                    if adapter is None:
                        raise RuntimeError("Geen actieve integratie voor dit apparaat")
                    action, params = cmd.action, dict(cmd.params)
                    if dev.provider == "gree" and action == "set_hvac":
                        if params.get("mode") == "off":
                            params = {"power": False}
                        else:
                            params = {"power": True, **params}
                    res = await adapter.execute(Command(dev.stable_identity, action, params))
                    cmd.status = "confirmed" if res.confirmed else ("sent" if res.ok else "failed")
                    cmd.detail = res.detail
                    if res.state and dev.provider == "gree":
                        mode = "off" if res.state.get("power") is False else res.state.get("mode")
                        dev.state = {
                            **(dev.state or {}),
                            **{k: v for k, v in res.state.items() if k != "raw"},
                            "mode": mode,
                        }
                        dev.last_seen, dev.online = datetime.now(UTC), True
                except PolicyDenied as e:
                    cmd.status, cmd.detail = "denied", e.message
                except Exception as e:
                    cmd.status, cmd.detail = "failed", str(e)[:500]
                if cmd.zone_id:
                    z = await db.get(ClimateZone, cmd.zone_id)
                    home = await db.get(Home, cmd.home_id)
                    if cmd.status in ("failed", "denied"):
                        db.add(ZoneLog(zone_id=z.id, kind="error", message=f"Commando niet uitgevoerd: {cmd.detail}"))
                    elif cmd.status == "sent":
                        db.add(ZoneLog(zone_id=z.id, kind="warn", message=f"Verzonden, niet bevestigd: {cmd.detail}"))
                    await events.publish(db, "zone.updated", home.id, await views.zone_view(db, z, home))
                await events.publish(
                    db,
                    "command.updated",
                    cmd.home_id,
                    {"id": cmd.id, "status": cmd.status, "detail": cmd.detail},
                )
                await db.commit()
