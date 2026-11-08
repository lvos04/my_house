"""Builds the JSON shapes from docs/api.md.

Split per area; everything is re-exported here so ``from shc_api import views`` and
``views.zone_view(...)`` keep working:

- ``common``  home shape, ENERGY_ROLES, unit caps/state, schedule + outdoor-unit lookups
- ``zones``   climate zone shape
- ``lights``  light groups and scenes
- ``energy``  energy series, tariffs, summary, live power
- ``status``  status checks, open alerts, system checks
"""

from .common import (
    ENERGY_ROLES,
    _measurement,
    caps_of,
    home_view,
    other_zone_modes,
    schedule_entries,
    unit_state,
)
from .energy import energy_series, energy_summary, live_power, tariff_at
from .lights import lights_view
from .status import open_alerts, status_checks, system_checks
from .zones import zone_view

__all__ = [
    "ENERGY_ROLES",
    "_measurement",
    "caps_of",
    "energy_series",
    "energy_summary",
    "home_view",
    "lights_view",
    "live_power",
    "open_alerts",
    "other_zone_modes",
    "schedule_entries",
    "status_checks",
    "system_checks",
    "tariff_at",
    "unit_state",
    "zone_view",
]
