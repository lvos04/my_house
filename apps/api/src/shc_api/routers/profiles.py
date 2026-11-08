"""Comfort profiles (verbeterlijst item 45).

To be implemented here: CRUD for a home's comfort profiles (``models.ComfortProfile``:
target, band, mode, energy priority, schedule), assigning a profile to a climate zone
(``ClimateZone.profile_id``) and applying/switching it, plus the watchdog and
energy-priority settings that belong with profiles. Experiment variants refer to
profiles so the worker can switch them automatically.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["profiles"])
