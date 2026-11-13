"""KLEIN-40 (vervolg op KLEIN-39): de controle van de apparaatsleutels bij ``shc-api verify-backup`` en
``shc-api restore`` (cli.py, ``_print_key_check``) ving alleen de RuntimeError van een ontbrekende
SHC_SECRET_KEY op. Een te korte sleutel gaf een ValueError uit SecretBox die doorviel tot een traceback.
Nu krijgt de beheerder bij een te korte sleutel dezelfde soort melding ("Apparaatsleutels niet
gecontroleerd: ...") met een vaste tekst, zonder de sleutel of een deel ervan, en loopt het commando
verder zoals bij een ontbrekende sleutel.
"""

import base64
from types import SimpleNamespace

import pytest

from shc_api import backup as bk
from shc_api import cli
from shc_api.config import get_settings
from shc_api.security import SecretBox

TE_KORT = "KortGeheimQ7zX-9wv"  # 18 tekens: SecretBox weigert hem


def _verified() -> SimpleNamespace:
    """Een gecontroleerde back-up met één gekoppeld apparaat (sleutel van deze server)."""
    blob = SecretBox(get_settings().resolved_secret_key()).encrypt({"key": "abc"})
    return SimpleNamespace(
        format_version=2,
        manifest={
            "created_at": "2026-01-01T00:00:00+00:00",
            "app_version": "test",
            "schema_revision": None,
            "tables": {},
            "zigbee2mqtt_included": False,
        },
        summary={
            "_integration_rows": [
                {"name": "lamp", "provider": "hue", "credentials_enc": {"$b64": base64.b64encode(blob).decode()}}
            ],
            "_secret_settings": [],
        },
    )


def _zet_sleutel(monkeypatch, sleutel: str | None) -> None:
    monkeypatch.setattr(get_settings(), "secret_key_file", None)
    monkeypatch.setattr(get_settings(), "secret_key", sleutel)


def _geen_sleutelmateriaal(tekst: str) -> None:
    assert TE_KORT not in tekst
    for i in range(len(TE_KORT) - 5):  # ook geen stuk van 6 tekens
        assert TE_KORT[i : i + 6] not in tekst


def test_te_korte_sleutel_geeft_melding_zonder_sleutel(monkeypatch, capsys):
    v = _verified()  # gemaakt met de sleutel van deze server
    _zet_sleutel(monkeypatch, TE_KORT)
    assert cli._print_key_check(v) is None
    uit = capsys.readouterr()
    assert "Apparaatsleutels niet gecontroleerd: SHC_SECRET_KEY is ongeldig" in uit.out
    assert "minimaal 32 tekens" in uit.out
    _geen_sleutelmateriaal(uit.out + uit.err)


def test_ontbrekende_sleutel_houdt_oude_melding(monkeypatch, capsys):
    v = _verified()
    _zet_sleutel(monkeypatch, None)
    assert cli._print_key_check(v) is None
    assert "Apparaatsleutels niet gecontroleerd: SHC_SECRET_KEY of SHC_SECRET_KEY_FILE ontbreekt" in (
        capsys.readouterr().out
    )


def test_goede_sleutel_controleert_de_apparaten(capsys):
    c = cli._print_key_check(_verified())
    assert c is not None and c["ok"] == 1 and c["total"] == 1
    assert "Apparaatsleutels: 1/1 te ontsleutelen" in capsys.readouterr().out


@pytest.mark.parametrize("sleutel", [TE_KORT, None], ids=["te_kort", "ontbreekt"])
def test_verify_backup_loopt_door_zonder_traceback(monkeypatch, capsys, tmp_path, sleutel):
    """Het hele commando: geen traceback, zelfde vervolg (controle afgerond, exitcode 0) als bij een
    ontbrekende sleutel."""
    v = _verified()
    _zet_sleutel(monkeypatch, sleutel)
    monkeypatch.setattr(bk, "verify_file", lambda path, pw: v)
    monkeypatch.setattr(bk, "check_restorable", lambda manifest: None)
    pw = tmp_path / "pw"
    pw.write_text("wachtwoordzin-voor-de-test\n")
    cli.main(["verify-backup", str(tmp_path / "x.shcbak"), "--passphrase-file", str(pw)])  # geen SystemExit
    uit = capsys.readouterr()
    assert "Apparaatsleutels niet gecontroleerd: " in uit.out
    assert "Controle geslaagd (er is niets gewijzigd)." in uit.out
    _geen_sleutelmateriaal(uit.out + uit.err)
