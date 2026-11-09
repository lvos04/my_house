"""Third-round follow-ups for the small items "Geheimen zijn op de NAS voor iedereen leesbaar" and
"Te ruime rechten": restoring a redacted backup keeps the real Zigbee2MQTT secret.yaml, and a pairing
address cannot be passed around the address check in the options."""

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from shc_api import backup as bk
from shc_api.config import get_settings

PW = "gap-wachtwoordzin-456"


async def _backup(tmp_path, z2m, include_secrets: bool):
    target = tmp_path / f"b-{include_secrets}.shcbak"
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as c:
            manifest = await bk.write_backup(c, target, PW, include_secrets, z2m_dir=z2m)
    finally:
        await engine.dispose()
    return target, manifest


async def test_restoring_a_redacted_backup_keeps_the_existing_zigbee2mqtt_secret(admin, tmp_path):
    z = tmp_path / "zigbee2mqtt"
    z.mkdir()
    (z / "configuration.yaml").write_text("mqtt:\n  password: '!secret.yaml mqtt_password'\n")
    (z / "secret.yaml").write_text('mqtt_password: "s3cret-mqtt"\n')
    redacted, manifest = await _backup(tmp_path, z, include_secrets=False)
    assert manifest["zigbee2mqtt"]["redacted"] == ["secret.yaml"]
    v = bk.verify_file(redacted, PW)

    # Back onto the NAS that still has its secret.yaml: the real password stays.
    live = tmp_path / "live"
    live.mkdir()
    (live / "secret.yaml").write_text('mqtt_password: "nieuw-wachtwoord"\n')
    assert bk.z2m_kept_secrets(v.manifest, live) == ["secret.yaml"]
    assert bk.extract_z2m(redacted, PW, live, v.manifest) == 1
    assert (live / "secret.yaml").read_text() == 'mqtt_password: "nieuw-wachtwoord"\n'
    assert "secret.yaml mqtt_password" in (live / "configuration.yaml").read_text()

    # Onto an empty directory (new NAS): the blanked file is written, so Zigbee2MQTT finds the key.
    fresh = tmp_path / "fresh"
    assert bk.z2m_kept_secrets(v.manifest, fresh) == []
    assert bk.extract_z2m(redacted, PW, fresh, v.manifest) == 2
    assert 'mqtt_password: ""' in (fresh / "secret.yaml").read_text()

    # A backup WITH secrets restores secret.yaml as it was in the backup.
    full, _ = await _backup(tmp_path, z, include_secrets=True)
    vf = bk.verify_file(full, PW)
    assert bk.z2m_kept_secrets(vf.manifest, live) == []
    assert bk.extract_z2m(full, PW, live, vf.manifest) == 2
    assert (live / "secret.yaml").read_text() == 'mqtt_password: "s3cret-mqtt"\n'


async def test_pairing_options_cannot_carry_the_address(admin, home):
    for host in ("172.30.251.3", "postgres", "192.168.1.20"):
        r = await admin.post(
            "/integrations/hue/pair",
            json={"home_id": str(home["id"]), "host": "192.168.1.20", "options": {"host": host}},
        )
        assert r.status_code == 422, r.text
        assert "veld 'host'" in r.text


# --- Kleine punten "Hue-scènes": deletions missed while the worker or the eventstream was down -----------


async def test_hue_devices_deleted_while_the_worker_was_down_are_archived(admin):
    import copy

    from shc_integrations.hue import HueAdapter
    from sqlalchemy import select

    from shc_api.db import sessionmaker
    from shc_api.models import Device

    from .ws_devices_support import add_integration, hue_inventory, make_worker, new_home

    inv = hue_inventory()

    class OfflineHue(HueAdapter):
        """The real adapter with its full reload served from a list (no bridge, no HTTP)."""

        def __init__(self, resources):
            super().__init__({"host": "192.168.1.30"}, {"application_key": "k"})
            self.items = resources

        async def load_resources(self):
            self.model.load(copy.deepcopy(self.items))
            return self._resources

    async def devices(home_id):
        async with sessionmaker()() as db:
            rows = (await db.execute(select(Device).where(Device.home_id == home_id))).scalars()
            return {d.stable_identity: d for d in rows}

    home_id = await new_home(admin)
    integ = await add_integration(home_id, "hue", config={"host": "192.168.1.30"}, identity="ecb5fafffe000001")
    w = make_worker()
    await w.handle_events(integ, await OfflineHue(inv.full_resources()).full_reload_events())
    before = await devices(home_id)
    assert {inv.L1, inv.L3, inv.S_NIGHT, inv.D_DIMMER} <= set(before)
    assert all(before[i].lifecycle == "active" for i in (inv.L3, inv.S_NIGHT))

    # The worker restarts; meanwhile the night light, the "Nacht" scene and the dimmer were deleted in
    # the Hue app. The worker seeds the new adapter with what the app knows; the first full reload
    # reports what is gone and the worker archives it.
    gone = {inv.L3, inv.D3, inv.S_NIGHT, inv.D_DIMMER}
    left = [
        r for r in inv.full_resources() if r["id"] not in gone and (r.get("owner") or {}).get("rid") not in gone
    ]  # a deleted device takes its services (buttons, battery, connectivity) with it
    adapter = OfflineHue(left)
    await w._seed_inventory(integ, adapter)
    await w.handle_events(integ, await adapter.full_reload_events())
    after = await devices(home_id)
    for rid in (inv.L3, inv.S_NIGHT, inv.D_DIMMER):
        d = after[rid]
        assert d.lifecycle == "archived" and d.diagnostics["archived_by"] == "adapter" and d.online is False
    assert after[inv.L1].lifecycle == "active"
