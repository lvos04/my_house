# Onderhoud, back-up, herstel en verhuizen

## Updates en terugdraaien

1. Maak eerst een back-up (**Systeem › Back-ups › Nu maken**).
2. Maak de YAML opnieuw met `--version <nieuwe versie>` en werk de app bij in TrueNAS (**Apps › smart-home › Edit**).
3. Bij het starten voert de API de databasemigraties uit. Elke migratie draait in één transactie: lukt hij niet, dan blijft de database zoals hij was en start de API niet (zichtbaar in de logs).
4. **Terugdraaien:** zet de vorige versie terug. Is er inmiddels een migratie uitgevoerd, herstel dan ook de back-up van stap 1 (de oude versie kent het nieuwe schema niet).

## Back-ups

- **Automatisch:** zet een wachtwoordzin in `<root>/app/backup_passphrase` (minimaal 12 tekens, eigenaar 10001). De worker maakt dan elke nacht om 03:00 een versleutelde back-up in `<root>/backups/` en bewaart er 14.
- **Handmatig:** via de web-app of `docker exec <api-container> shc-api backup`.
- **Inhoud:** alle instellingen, gebruikers, apparaten, rollen, metingen, schema's en tarieven, de Zigbee2MQTT-gegevens (netwerksleutel en apparatenlijst) en, tenzij je *geheimen weglaten* kiest, de versleutelde apparaatsleutels.
- **Versleuteling:** AES-256-GCM met een sleutel uit je wachtwoordzin (scrypt). Elk bestand in het archief heeft een SHA-256-checksum in het manifest. Een beschadigd of aangepast bestand wordt geweigerd.
- **Bewaar** de wachtwoordzin én `secrets/secret_key` buiten de NAS (wachtwoordmanager). Zonder `secret_key` moeten Hue en HomeWizard v2 opnieuw gekoppeld worden.
- Daarnaast: TrueNAS-snapshots van de dataset en een externe replicatie.

## Herstellen

```bash
docker exec -it <api-container> shc-api verify-backup /backups/shc-YYYYMMDD-HHMMSS.shcbak
```

Dit controleert alleen en wijzigt niets. Echt herstellen:

```bash
docker exec -it <api-container> shc-api restore /backups/shc-YYYYMMDD-HHMMSS.shcbak --z2m-dir /data/zigbee2mqtt-restore
```

Daarna staat de app in **veilige modus**: geen enkel apparaatcommando tot je in **Systeem** bevestigt dat de apparaten kloppen. Zo schakelt een herstel nooit onverwacht iets.

Zet bij Zigbee de bestanden uit `--z2m-dir` terug in `<root>/zigbee2mqtt/` voordat Zigbee2MQTT start. Zitten ze niet in de back-up, dan moeten de sensoren opnieuw gekoppeld worden.

## Herstel oefenen (aanbevolen elk kwartaal)

1. Start een tweede, tijdelijke stack op een andere map: `install.sh /tmp/shc-test` en `SHC_ROOT=/tmp/shc-test SHC_HTTP_PORT=8099 docker compose -p shc-test -f infra/compose/compose.yaml up -d`.
2. Kopieer de laatste back-up naar `/tmp/shc-test/backups/` en voer `restore` uit in die stack.
3. Vergelijk in **Systeem › Back-ups** de aantallen records met de originele back-up. `verify-backup` toont ze ook.
4. Ruim op met `docker compose -p shc-test down -v` en verwijder `/tmp/shc-test`.

## Verhuizen

**A. Nieuwe server, zelfde huis:** installeer op de nieuwe server, herstel de back-up, kopieer `secrets/secret_key` mee. Apparaten worden herkend op serienummer/MAC, niet op IP. Controleer **Apparaten** en hef de veilige modus op.

**B. Nieuw huis:**

1. Herstel de back-up op de server in het nieuwe huis.
2. **Systeem › Woningen**: archiveer de oude woning. De historie blijft, gekoppeld aan de oude meters en het oude huis.
3. Maak de nieuwe woning aan. Schema's en comfortprofielen kun je overnemen; kalibratie en het adaptieve model beginnen opnieuw.
4. Koppel apparaten opnieuw (nieuw netwerk). Vervangen apparaten koppel je aan dezelfde rol, bijvoorbeeld *Airco totaal*; de oude binding wordt gesloten, niet verwijderd.
5. Functies die het nieuwe huis niet heeft (bijvoorbeeld geen tweede airco), zijn grijs met uitleg.

## Hoofdsleutel vervangen

`secret_key` versleutelt de apparaatsleutels. Vervangen: maak een back-up mét geheimen, zet een nieuwe `secret_key`, herstel, en koppel Hue en HomeWizard v2 opnieuw (de oude versleutelde waarden zijn dan onleesbaar). Sessies en telefoon-sleutels blijven geldig.

## Wachtwoord vergeten

```bash
docker exec -it <api-container> shc-api reset-password <gebruikersnaam>
```
