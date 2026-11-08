"""Provider name -> adapter class, plus Dutch manufacturer instructions for the setup wizard."""

from __future__ import annotations

from dataclasses import dataclass

from .base import Adapter, PairingInstructions
from .gree.adapter import INSTRUCTIONS as GREE_INSTRUCTIONS
from .gree.adapter import GreeAdapter
from .homewizard.adapter import INSTRUCTIONS as HW_INSTRUCTIONS
from .homewizard.adapter import HomeWizardAdapter
from .hue.adapter import INSTRUCTIONS as HUE_INSTRUCTIONS
from .hue.adapter import HueAdapter
from .zigbee.adapter import INSTRUCTIONS as ZIGBEE_INSTRUCTIONS
from .zigbee.adapter import ZigbeeAdapter


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    provider: str
    label: str
    adapter: type[Adapter]
    instructions: PairingInstructions
    needs_manufacturer_app: str | None  # what must happen in the vendor's app first (None = nothing)
    works_locally: str


PROVIDERS: dict[str, ProviderInfo] = {
    "hue": ProviderInfo(
        "hue",
        "Philips Hue",
        HueAdapter,
        HUE_INSTRUCTIONS,
        needs_manufacturer_app="Lampen en knoppen eerst in de Hue-app aan de bridge toevoegen.",
        works_locally="Lampen, groepen, scènes en knop-events via de lokale API v2 van de bridge.",
    ),
    "gree": ProviderInfo(
        "gree",
        "Gree airco",
        GreeAdapter,
        GREE_INSTRUCTIONS,
        needs_manufacturer_app="Eenmalig wifi-koppeling via de Gree+-app (2,4 GHz).",
        works_locally="Aan/uit, modus, doeltemperatuur, ventilator en swing via UDP, mits model en firmware "
        "het lokale protocol ondersteunen (nog niet getest met echte hardware).",
    ),
    "homewizard": ProviderInfo(
        "homewizard",
        "HomeWizard",
        HomeWizardAdapter,
        HW_INSTRUCTIONS,
        needs_manufacturer_app="‘Lokale API’ aanzetten in de HomeWizard Energy-app.",
        works_locally="Vermogen, kWh-standen (import/export, per tarief) en gas via de lokale API v1/v2.",
    ),
    "zigbee": ProviderInfo(
        "zigbee",
        "Zigbee (via Zigbee2MQTT)",
        ZigbeeAdapter,
        ZIGBEE_INSTRUCTIONS,
        needs_manufacturer_app=None,
        works_locally="Temperatuur, luchtvochtigheid, batterij en signaal via de eigen USB-dongle.",
    ),
}

ADAPTERS: dict[str, type[Adapter]] = {name: info.adapter for name, info in PROVIDERS.items()}


def instructions_for(provider: str) -> PairingInstructions:
    try:
        return PROVIDERS[provider].instructions
    except KeyError as exc:
        raise KeyError(f"Onbekende provider '{provider}'") from exc
