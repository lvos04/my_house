"""Ook de normale schema- en connectorcontrole bij opstarten laat de leesbron beschikbaar."""
import asyncio
import secrets
from datetime import UTC, datetime, timedelta

import pytest

from shc_api import cli, migration_source
from shc_api.config import get_settings
from shc_api.main import create_app

from .test_herstelpoorten import proefinstallatie
from .test_ws_backup_restore import _scalar


async def test_aanmelden_na_bronherstart(tmp_path, monkeypatch):
    cfg = get_settings()
    zigbee = tmp_path / "zigbee2mqtt"
    zigbee.mkdir()
    monkeypatch.setattr(cfg, "data_dir", tmp_path / "app")
    monkeypatch.setattr(cfg, "zigbee2mqtt_dir", zigbee)
    async with proefinstallatie(monkeypatch) as beheer:
        pw = secrets.token_urlsafe(24)
        assert (await beheer.post("/auth/users", {"username": "lezen", "role": "viewer", "password": pw})).is_success
        assert (await beheer.post("/system/restore/source/pause")).is_success
        with pytest.raises(PermissionError):
            (zigbee / "database.db").write_text("tijdens de bronpauze")
        beheer_cookie = dict(beheer.c.cookies)
        url = cfg.database_url
        await asyncio.to_thread(cli.migrate, url)
        app = create_app()
        async with app.router.lifespan_context(app):
            assert (await beheer.c.post("/api/v1/auth/login", json={"username": "lezen", "password": pw})).is_success
            for _ in range(cfg.login_max_attempts):
                r = await beheer.c.post("/api/v1/auth/login", json={"username": "lezen", "password": "fout"})
                assert r.status_code == 401
            r = await beheer.c.post("/api/v1/auth/login", json={"username": "lezen", "password": pw})
            assert r.status_code == 429
        assert await _scalar(url, "SELECT failed_logins FROM users WHERE username='lezen'") == 0

        class NaVerval(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(UTC) + timedelta(hours=2)

        monkeypatch.setattr(migration_source, "datetime", NaVerval)
        beheer.c.cookies.clear()
        beheer.c.cookies.update(beheer_cookie)
        assert (await beheer.post("/system/restore/source/resume")).is_success
        (zigbee / "database.db").write_text("na hervatten")
        assert await _scalar(url, "SELECT failed_logins FROM users WHERE username='lezen'") == cfg.login_max_attempts
        assert await _scalar(url, "SELECT count(*) FROM audit_log WHERE action='user_locked' AND actor='lezen'") == 1
