"""Scheduled automatic backups (needs <data_dir>/backup_passphrase)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import Settings
from ..db import sessionmaker
from ..store import get_setting, set_setting

log = logging.getLogger("shc.worker")  # same logger as core.log


class BackupMixin:
    # Provided by core.Worker.
    settings: Settings

    async def backup_round(self) -> None:
        s = self.settings
        pf = s.data_dir / "backup_passphrase"
        if not pf.exists():
            return
        async with sessionmaker()() as db:
            last = await get_setting(db, "last_auto_backup")
            if last and datetime.now(UTC) - datetime.fromisoformat(last) < timedelta(hours=23):
                return
            local_hour = datetime.now(ZoneInfo("Europe/Amsterdam")).hour
            if last and local_hour != 3:
                return
            from ..routers.system import make_backup

            rec = await make_backup(db, pf.read_text().strip(), include_secrets=True)
            await set_setting(db, "last_auto_backup", datetime.now(UTC).isoformat())
            await db.commit()
            log.info("Automatische back-up: %s", rec.filename)
            keep = int(json.loads(json.dumps(await get_setting(db, "backup_keep", 14))))
            files = sorted(s.backup_dir.glob("shc-*.shcbak"))
            for old in files[:-keep]:
                old.unlink(missing_ok=True)
