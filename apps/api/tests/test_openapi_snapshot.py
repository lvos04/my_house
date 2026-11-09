"""Driftcontrole van de OpenAPI-momentopname (PLATFORM_CONTRACTEN.md §5: "CI faalt bij drift").

``docs/openapi/my_house.openapi.json`` is de export van ``create_app().openapi()`` (INVENTARIS.md §5). Een
wijziging aan routes, schema's of foutformaat die niet in de momentopname staat, laat deze test falen. Een bewuste
API-wijziging werkt de momentopname in dezelfde commit bij (zie de docstring hieronder voor het commando).
Alleen-uiterlijk-rondes (zoals de Peil-omzetting) mogen hem nooit raken.
"""

from __future__ import annotations

import json
from pathlib import Path

from shc_api.main import create_app

SNAPSHOT = Path(__file__).resolve().parents[3] / "docs" / "openapi" / "my_house.openapi.json"


def export() -> str:
    """Zelfde serialisatie als de momentopname. Bijwerken (alleen bij een bewuste API-wijziging):
    python -I -c "import json; from shc_api.main import create_app; print(json.dumps(create_app().openapi(),
    indent=2, sort_keys=True, ensure_ascii=False), end='')" > docs/openapi/my_house.openapi.json"""
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True, ensure_ascii=False)


def test_openapi_export_matches_snapshot() -> None:
    got = export()
    want = SNAPSHOT.read_text(encoding="utf-8")
    if got != want:
        got_paths = set(json.loads(got)["paths"])
        want_paths = set(json.loads(want)["paths"])
        raise AssertionError(
            "OpenAPI wijkt af van docs/openapi/my_house.openapi.json "
            f"(nieuw: {sorted(got_paths - want_paths)}, weg: {sorted(want_paths - got_paths)}; "
            "verder verschillen in schema's of beschrijvingen). Werk de momentopname alleen bij voor een bewuste "
            "API-wijziging."
        )
