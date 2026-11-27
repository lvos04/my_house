"""Losse Zigbee-bestanden blijven bytegelijk in een afzonderlijke, afgeschermde herstelmap."""

import asyncio
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException, Request

from shc_api.routers import system


@pytest.fixture
def proef(tmp_path, monkeypatch):
    actief = tmp_path / 'zigbee2mqtt'
    actief.mkdir()
    (actief / 'database.db').write_bytes(b'bestaande proefwoning\n')
    cfg = SimpleNamespace(data_dir=tmp_path, zigbee2mqtt_dir=actief, max_upload_bytes=4096)
    monkeypatch.setattr(system, 'get_settings', lambda: cfg)
    monkeypatch.setattr(system, 'audit', AsyncMock())

    def post(bestanden, *, afgekapt=False):
        pakket = httpx.Request('POST', 'http://proef.test', files=[('file', (n, b)) for n, b in bestanden])
        inhoud = pakket.read()
        if afgekapt:
            inhoud = inhoud[:-20]
        # Laat bestandskoppen en afsluiters over de grenzen van kleine netwerkblokken vallen.
        blokken = iter(inhoud[i:i + 11] for i in range(0, len(inhoud), 11))

        async def ontvang():
            blok = next(blokken, b'')
            return {'type': 'http.request', 'body': blok, 'more_body': bool(blok)}

        request = Request({'type': 'http', 'headers': [(k.lower(), v) for k, v in pakket.headers.raw]}, ontvang)
        return asyncio.run(system.restore_zigbee_files(request, SimpleNamespace(commit=AsyncMock()),
                                                      SimpleNamespace(actor='beheerder')))

    return cfg, post


def test_aparte_kopie_met_herhaling_en_ongewijzigde_bron(tmp_path, proef):
    cfg, post = proef
    bestanden = {'configuration.yaml': b'version: 4\n', 'database.db': b'proefapparaat\n'}
    resultaat = post(bestanden.items())
    doel = tmp_path / resultaat['map']
    assert doel.parent == tmp_path / 'zigbee-herstel' and stat.S_IMODE(doel.stat().st_mode) == 0o700
    assert resultaat['bestanden'] == sorted(bestanden)
    assert {p.name: p.read_bytes() for p in doel.iterdir()} == bestanden
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in doel.iterdir())
    assert post(reversed(list(bestanden.items()))) == resultaat
    assert list(doel.parent.iterdir()) == [doel]
    (doel / 'database.db').write_bytes(b'handmatig bewaard')
    with pytest.raises(HTTPException) as fout:
        post(bestanden.items())
    assert fout.value.status_code == 409 and (doel / 'database.db').read_bytes() == b'handmatig bewaard'
    assert {p.name: p.read_bytes() for p in cfg.zigbee2mqtt_dir.iterdir()} == {'database.db': b'bestaande proefwoning\n'}


@pytest.mark.parametrize(('geval', 'status'), [('onbekend', 422), ('dubbel', 422), ('omvang', 413),
                                                ('actieve_map', 409), ('afgekapt', 422)])
def test_onbruikbare_invoer_publiceert_geen_kopie(tmp_path, proef, geval, status):
    cfg, post = proef
    bron = cfg.zigbee2mqtt_dir / 'database.db'
    bestanden = [('database.db', b'proef'), ('state.json', b'{}')]
    root = tmp_path / 'zigbee-herstel'
    if geval == 'actieve_map':
        cfg.zigbee2mqtt_dir = root
    if geval == 'onbekend':
        bestanden.append(('los.txt', b'onbekend'))
    if geval == 'dubbel':
        bestanden.append(bestanden[0])
    if geval == 'omvang':
        cfg.max_upload_bytes = 80
    with pytest.raises(HTTPException) as fout:
        post(bestanden, afgekapt=geval == 'afgekapt')
    assert fout.value.status_code == status and (not root.exists() or not list(root.iterdir()))
    assert bron.read_bytes() == b'bestaande proefwoning\n'
