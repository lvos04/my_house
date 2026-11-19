from pathlib import Path


def test_wachtwoord_en_zigbee_zonder_terminal():
    doc = (Path(__file__).resolve().parents[3] / "docs/onderhoud.md").read_text()
    wachtwoord = doc.split("## Wachtwoord vergeten\n")[1].split("\n## ")[0]
    assert "reset-password" not in wachtwoord
    assert "geen zelfherstel" in wachtwoord
    assert "shc-api reset-password" not in doc.split("## Herstellen")[0]
    wizard = doc.split("## Herstellen\n")[1].split("### Database-dump terugzetten")[0]
    assert "Zigbee-bestanden" in wizard and "ontbreekt een beproefd webherstel" in wizard
    assert all(cmd not in wizard for cmd in ("shc-api", "docker exec", "pg_restore"))
