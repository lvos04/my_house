"""Tests van de platformdocumenten (contractkopie, Peil-kopie, EVIDENCE.json): geen database nodig.

De sessie-fixture ``database`` uit ``tests/conftest.py`` maakt een PostgreSQL-database aan; deze map overschrijft hem
met een lege fixture, zodat ``pytest tests/platform_v2`` ook zonder ``SHC_TEST_PG`` draait. In de volledige suite
(CI, backend-job) draaien deze tests gewoon mee.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    yield


@pytest.fixture
def repo() -> Path:
    return REPO
