"""Alleen een door deze installatie bekrachtigde dump mag via de webapp naar PostgreSQL."""

import hashlib
import io
import os
import secrets
from unittest.mock import AsyncMock, MagicMock

import pytest

from shc_api import backup as bk
from shc_api.config import get_settings

from .test_ws_backup_api import PW


def test_herkomst_bindt_werkelijke_dump_en_installatie(tmp_path, monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, 'secret_key_file', None)
    monkeypatch.setattr(cfg, 'secret_key', secrets.token_urlsafe(48))
    monkeypatch.setattr(bk, 'check_archive', AsyncMock())
    geldig = bk.PGDMP_MAGIC + b'proefgegevens'
    zegel = bk.dumpzegel(hashlib.sha256(geldig).digest())

    def schrijf(payload, oorsprong):
        raw = io.BytesIO()
        schrijver = bk.EncryptingWriter(raw, PW, '2026-01-02', content=bk.DUMP_CONTENT, dumpzegel=oorsprong)
        schrijver.write(payload)
        schrijver.finish()
        pad = tmp_path / 'proef.shcdump'
        pad.write_bytes(raw.getvalue())
        return pad

    pad = schrijf(geldig, zegel)
    assert bk.controleer_webdump(pad, PW) == (len(geldig), True)
    for data, oorsprong in ((geldig, None), (geldig + b'gewijzigd', zegel)):
        pad = schrijf(data, oorsprong)
        assert bk.controleer_webdump(pad, PW) == (len(data), False)
        with pytest.raises(bk.BackupError, match='herkomst'):
            bk.decrypt_dump(pad, PW, tmp_path / 'uitvoer', vereis_herkomst=True)
        assert not (tmp_path / 'uitvoer').exists()
    pad = schrijf(geldig, zegel)
    monkeypatch.setattr(cfg, 'secret_key', secrets.token_urlsafe(48))
    assert bk.controleer_webdump(pad, PW) == (len(geldig), False)


def test_uitvoer_van_pg_dump_krijgt_herkomstbewijs(tmp_path, monkeypatch):
    import subprocess

    cfg = get_settings()
    monkeypatch.setattr(cfg, 'secret_key_file', None)
    monkeypatch.setattr(cfg, 'secret_key', secrets.token_urlsafe(48))
    archief = bk.PGDMP_MAGIC + b'gesimuleerde PostgreSQL-uitvoer'

    def proces(args, *, stdout, **kwargs):
        assert '--format=custom' in args and kwargs.get('shell') is None
        os.write(stdout.fileno(), archief)
        proef = MagicMock()
        proef.__enter__.return_value = proef
        proef.communicate.return_value = (None, b'')
        proef.returncode = 0
        return proef

    monkeypatch.setattr(subprocess, 'Popen', proces)
    monkeypatch.setattr(bk, 'check_archive', AsyncMock())
    pad = tmp_path / 'losse.shcdump'
    info = bk._pg_dump_sync(cfg.database_url, pad, PW, '2026-01-02')
    assert info['plain_bytes'] == len(archief) and info['file_size'] == pad.stat().st_size
    assert bk.controleer_webdump(pad, PW) == (len(archief), True)
