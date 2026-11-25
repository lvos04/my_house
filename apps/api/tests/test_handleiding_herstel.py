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


def test_herstelproef_en_oude_sleutel_volgen_de_webapp():
    docs = Path(__file__).resolve().parents[3] / "docs"
    onderhoud = (docs / "onderhoud.md").read_text().split("## Alleen voor ontwikkeling\n")[0]
    oefenen = onderhoud.split("## Herstel oefenen", 1)[1].split("\n## ", 1)[0]
    sleutel = onderhoud.split("## Hoofdsleutel vervangen\n", 1)[1].split("\n## ", 1)[0]
    for bediening in ("Uploaden en controleren", "Herstelproef uitvoeren", "Resultaat per tabel", "Annuleren"):
        assert bediening in oefenen
    for bediening in ("Apparaatsleutels herstellen", "Bestand met de oude hoofdsleutel", "Oude hoofdsleutel uploaden"):
        assert bediening in sleutel
    for route in (onderhoud, (docs / "problemen-oplossen.md").read_text()):
        assert all(opdracht not in route for opdracht in ("docker exec", "docker compose", "sudo ", "shc-api rekey",
                                                          "shc-api restore", "createdb ", "pg_restore", "install.sh"))
