"""Gereedheid en Zigbee-diagnose via de API en de worker van de Custom App."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, update

from shc_api.db import sessionmaker
from shc_api.herstel import HerstelApp
from shc_api.models import Integration

from .test_ws_devices_core import FakeAdapter
from .ws_devices_support import add_integration, make_worker


@pytest.fixture
async def zigbee_in_de_woning(home):
    created = []

    async def add(config=None):
        integ = await add_integration(uuid.UUID(home["id"]), "zigbee", config=config)
        created.append(integ.id)
        return integ

    yield add
    async with sessionmaker()() as db:
        await db.execute(delete(Integration).where(Integration.id.in_(created)))
        await db.commit()


async def test_readiness_geeft_contractfout_en_herstelinfo(client, app):
    from shc_api.db import get_session

    assert (await client.get("/readyz")).status_code == 200
    assert "/readyz" in app.openapi()["paths"]
    class Unavailable:
        async def execute(self, *_):
            raise ConnectionError
    app.dependency_overrides[get_session] = Unavailable
    try:
        unavailable = await client.get("/readyz")
    finally:
        del app.dependency_overrides[get_session]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=HerstelApp("later", "nu")),
                                 base_url="http://test") as http:
        recovery = await http.get("/readyz")
    for response, code in ((unavailable, "database_unavailable"), (recovery, "database_newer")):
        assert response.status_code == 503 and int(response.headers["Retry-After"]) > 0
        data = response.json()
        assert data["code"] == code and data["message"] and isinstance(data["details"], dict)
        assert uuid.UUID(data["trace_id"])


async def test_oude_interne_brokernaam_volgt_de_ingebouwde_broker(zigbee_in_de_woning, monkeypatch, tmp_path):
    monkeypatch.setenv("SHC_MQTT_COMPOSE_ALIAS", "true")
    monkeypatch.setenv("SHC_MQTT_HOST", "localhost")
    w = make_worker()
    w.settings = type(w.settings)()
    mqtt = tmp_path / "mqtt"
    mqtt.write_text(secrets.token_urlsafe(32))
    monkeypatch.setattr(w.settings, "mqtt_password_file", mqtt)
    for host, expected in (("mosquitto", "localhost"), ("externe-broker.invalid", "externe-broker.invalid")):
        integ = await zigbee_in_de_woning({"host": host, "port": 1883})
        try:
            assert await w.start_integration(integ, FakeAdapter)
            assert w.adapters[integ.id].config["host"] == expected
            assert ("password" in w.adapters[integ.id].credentials) is (expected == "localhost")
            assert integ.config["host"] == host
        finally:
            await w.stop_integration(integ.id, "einde proef")


async def test_verouderde_zigbeemeting_geeft_geen_groene_dongle(admin, zigbee_in_de_woning):
    integ = await zigbee_in_de_woning()
    for when in (None, "ongeldig", (datetime.now(UTC) - timedelta(hours=1)).isoformat()):
        health = {"checked_at": when, "extra": {"mqtt_connected": True, "bridge_online": True}}
        async with sessionmaker()() as db:
            await db.execute(update(Integration).where(Integration.id == integ.id).values(diagnostics={"health": health}))
            await db.commit()
        checks = {c["key"]: c for c in (await admin.get("/system/status")).json()["checks"]}
        assert all(checks[f"integration:{integ.id}:{part}"]["state"] == "unknown" for part in ("mqtt", "dongle"))
