"""Shared helpers for the devices-stream tests (test_ws_devices_*.py)."""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from shc_api.config import get_settings
from shc_api.db import sessionmaker
from shc_api.models import Device, Integration
from shc_api.security import SecretBox

INTEGRATION_TESTS = Path(__file__).resolve().parents[3] / "integrations" / "tests"


def hue_inventory():
    """The adapter stream's documented Hue inventory (integrations/tests/hue_inventory.py)."""
    if str(INTEGRATION_TESTS) not in sys.path:
        sys.path.append(str(INTEGRATION_TESTS))
    import hue_inventory

    return hue_inventory


def hue_id(n: int, prefix: str = "9") -> str:
    """A real-looking 36-character Hue v2 resource id."""
    return f"{prefix * 8}-0000-4000-8000-{n:012d}"


async def new_home(admin, name: str | None = None) -> uuid.UUID:
    r = await admin.post("/homes", json={"name": name or f"Apparatentest {uuid.uuid4().hex[:6]}"})
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["id"])


async def add_integration(
    home_id: uuid.UUID,
    provider: str,
    name: str | None = None,
    config: dict | None = None,
    creds: dict | None = None,
    identity: str | None = None,
    enabled: bool = True,
    raw_credentials: bytes | None = None,
) -> Integration:
    async with sessionmaker()() as db:
        i = Integration(
            home_id=home_id,
            provider=provider,
            name=name or provider,
            config=config or {},
            credentials_enc=raw_credentials
            if raw_credentials is not None
            else (SecretBox(get_settings().resolved_secret_key()).encrypt(creds) if creds else None),
            identity=identity,
            enabled=enabled,
            state="warn",
            detail="",
            diagnostics={},
        )
        db.add(i)
        await db.commit()
        return i


async def add_device(home_id: uuid.UUID, kind: str, name: str, provider: str = "test", **values) -> Device:
    async with sessionmaker()() as db:
        d = Device(
            home_id=home_id,
            provider=provider,
            kind=kind,
            name=name,
            stable_identity=values.pop("stable_identity", uuid.uuid4().hex),
            state=values.pop("state", {}),
            online=values.pop("online", True),
            last_seen=values.pop("last_seen", datetime.now(UTC)),
            capabilities=[],
            calibration={},
            diagnostics={},
            **values,
        )
        db.add(d)
        await db.commit()
        return d


async def fetch(model, id_):
    async with sessionmaker()() as db:
        return await db.get(model, id_)


def make_worker(tmp_path=None):
    from shc_api.worker import Worker

    w = Worker()
    if tmp_path is not None:
        w.alive_file = Path(tmp_path) / "shc-worker-alive"
    return w


def attach(w, integ: Integration, adapter) -> None:
    w.adapters[integ.id] = adapter
    w.integrations[integ.id] = integ
