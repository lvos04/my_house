"""Installatiebrede inbedrijfstelling, zonder automatische testbevestiging."""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting

KEY = "commissioning_gate"
LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended('shc-commissioning', 0))"


async def lock_commissioning(db: AsyncSession) -> None:
    """Altijd vóór apparaat- en buitenunitlocks; geldt tot commit of rollback."""
    await db.execute(text(LOCK_SQL))


def gate_state(value) -> dict:
    if (
        isinstance(value, dict)
        and type(value.get("version")) is int
        and value["version"] == 1
        and type(value.get("enabled")) is bool
        and isinstance(value.get("origin"), str)
        and value["origin"] in {"fresh", "existing_devices"}
        and (value["enabled"] or value["origin"] == "existing_devices")
    ):
        return {**value, "known": True}
    return {
        "version": 1, "enabled": True, "known": False, "origin": "unknown",
        "enabled_at": None, "enabled_by": None,
    }


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
