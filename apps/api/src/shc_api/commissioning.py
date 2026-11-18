"""Installatiebrede inbedrijfstelling, zonder automatische testbevestiging."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Device, Home, Setting
from .policy import DEFAULT_PROTECTED_ROLES, DeviceFacts
from .store import audit, get_setting, roles_of

KEY = "commissioning_gate"
TEST_CONFIRMED_ACTION = "device_marked_tested"
LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended('shc-commissioning', 0))"
SHARED_LOCK_SQL = "SELECT pg_advisory_xact_lock_shared(hashtextextended('shc-commissioning', 0))"


async def lock_commissioning(db: AsyncSession) -> None:
    """Exclusief voor installatiebrede wijzigingen, vóór woning- en buitenunitlocks."""
    await db.execute(text(LOCK_SQL))


async def lock_commissioning_shared(db: AsyncSession) -> None:
    await db.execute(text(SHARED_LOCK_SQL))


async def lock_home_commands(db: AsyncSession, home_id: uuid.UUID, *, skip_busy: bool = False) -> bool:
    """Deel de installatielock en houd uitvoering per woning exclusief tot commit of rollback."""
    await lock_commissioning_shared(db)
    key = {"name": f"shc-commissioning-home:{home_id}"}
    if skip_busy:
        return bool(await db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:name, 0))"), key))
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"), key)
    return True


def gate_state(value) -> dict:
    if (
        isinstance(value, dict)
        and type(value.get("version")) is int
        and value["version"] == 1
        and type(value.get("enabled")) is bool
        and isinstance(value.get("origin"), str)
        and value["origin"] in {"fresh", "existing_devices"}
        and (value["enabled"] or value["origin"] == "existing_devices")
        and _valid_activation(value)
    ):
        return {**value, "known": True}
    return {
        "version": 1, "enabled": True, "known": False, "origin": "unknown",
        "enabled_at": None, "enabled_by": None,
    }


def _valid_activation(value: dict) -> bool:
    if "enabled_at" not in value or "enabled_by" not in value:
        return False
    at, by = value["enabled_at"], value["enabled_by"]
    if not value["enabled"]:
        return at is None and by is None
    if not isinstance(at, str) or not isinstance(by, str) or not by.strip():
        return False
    try:
        return datetime.fromisoformat(at).utcoffset() is not None
    except ValueError:
        return False


async def read_gate(db: AsyncSession) -> dict:
    # Geen identity-map/cache: de andere transactie kan de gate net hebben ingeschakeld.
    raw = (await db.execute(select(Setting.value).where(Setting.key == KEY))).scalar_one_or_none()
    return gate_state(raw.get("v") if isinstance(raw, dict) else None)


def restored_gate(prior: dict | None, loaded: dict, mode: str, at: str) -> dict:
    """Eerder aan blijft aan; alleen de instelling draagt het herstelbesluit.

    ``prior=None`` betekent uitsluitend een nog niet ingevoerd schema zonder
    gate-instelling. Ontbrekend ná invoering is daarentegen onbekend en dus aan.
    """
    keep_before = prior is not None and prior["enabled"]
    chosen = prior if keep_before else loaded
    return {
        **{k: v for k, v in chosen.items() if k != "known"},
        "restored": {
            "at": at,
            "mode": mode,
            "source": "before" if keep_before else "backup",
            "enabled_before": prior["enabled"] if prior is not None else None,
            "known_before": prior["known"] if prior is not None else None,
            "enabled_loaded": loaded["enabled"],
            "known_loaded": loaded["known"],
        },
    }


async def command_facts(db: AsyncSession, device: Device, *, safe_mode: bool = False) -> DeviceFacts:
    await lock_home_commands(db, device.home_id)
    await db.refresh(device)
    roles, protected = await roles_of(db, device.id)
    return DeviceFacts(
        device.kind,
        roles,
        protected,
        bool(await get_setting(db, "safe_mode", False)) if safe_mode else False,
        effective_compatibility(device),
        (await read_gate(db))["enabled"],
        current_protocol_failure(device),
    )


def invalidate_device_test(device: Device) -> None:
    """Trek de actuele bevestiging in en behoud de bestaande auditgeschiedenis."""
    device.compatibility = "untested"
    device.tested_at = None
    device.diagnostics = {k: v for k, v in (device.diagnostics or {}).items() if k != "tested"}


def current_protocol_failure(device: Device) -> bool:
    pair = (device.diagnostics or {}).get("pair")
    return (isinstance(pair, dict) and pair.get("result") == "failed"
            and pair.get("code") == "unsupported" and not pair.get("protocol_read_ok_at"))


def effective_compatibility(device: Device) -> str:
    return "unsupported" if current_protocol_failure(device) else device.compatibility


def confirmation_block(device: Device, home: Home) -> str | None:
    if home.active_to is not None or device.lifecycle != "active":
        return "inactive_device"
    if effective_compatibility(device) == "unsupported":
        return "unsupported_device"
    return None


async def is_protected(db: AsyncSession, device: Device) -> bool:
    roles, protected = await roles_of(db, device.id)
    return bool(protected or roles & DEFAULT_PROTECTED_ROLES)


async def record_test_confirmation(
    db: AsyncSession, device: Device, *, actor: str, username: str, note: str | None,
) -> None:
    """Leg alleen een bewuste bevestiging vast onder de inbedrijfstellingsvergrendeling."""
    now = datetime.now(UTC)
    device.tested_at = now
    device.compatibility = "tested"
    device.diagnostics = {
        **(device.diagnostics or {}), "tested": {"at": now.isoformat(), "by": username, "note": note},
    }
    await audit(
        db, actor, TEST_CONFIRMED_ACTION, device.name, device=str(device.id), home=str(device.home_id),
        model=device.model, firmware=device.firmware,
    )
