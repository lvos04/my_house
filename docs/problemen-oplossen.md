# Problemen oplossen

`<api-container>` is de naam van de API-container (`docker ps`; op TrueNAS meestal `ix-smart-home-api-1`).
Meer over back-ups, herstel en sleutels: [onderhoud.md](onderhoud.md).

## Installatie en starten

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| App start niet, log: `SHC_SECRET_KEY … ontbreekt` | `install.sh` niet uitgevoerd | Voer `install.sh <root>` uit en maak de YAML opnieuw |
| `install.sh` zegt `LET OP` over rechten of ACL's | Dataset met preset *Apps* of *SMB* | Zie [installatie-truenas.md](installatie-truenas.md#1-dataset-maken): preset *Generic* of ACL weghalen, daarna `install.sh` opnieuw |
| TrueNAS: *Pool overlaps with other one* | Een andere app gebruikt dezelfde interne netwerken | Maak de YAML opnieuw met `--net-prefix 10.251` (of een ander vrij privé-bereik) |
| *De app start of wordt bijgewerkt…* blijft staan | API start niet | Logboek van de container `api`. Staat er *Database-update geweigerd*, dan is de automatische dump vóór de migratie mislukt (vaak: schijf vol); de database is niet aangeraakt. Los de oorzaak op en herstart |
| Ik kom na het beheerdersaccount niet verder | Nog geen woning | Vul het formulier *Woning aanmaken* in; daarna helpt de installatiewizard (`/installatie`) |

## Achtergronddienst, back-ups en meldingen

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| Melding *Achtergronddienst reageert niet*, of in **Systeem** *Achtergronddienst — geen hartslag* | Worker-container draait niet of hangt | TrueNAS › Apps › smart-home › logs van `worker`. Een vastgelopen lus stopt de worker zelf en Docker start hem opnieuw; staat hij op *Exited*, start de app dan opnieuw |
| *Achtergronddienst*: *Onderhoudsmodus* | Een herstel loopt (of `shc-api maintenance on` staat nog aan) | Wacht tot het herstel klaar is, of `docker exec -it <api-container> shc-api maintenance off` |
| Melding *Automatische back-up mislukt* | Zie de tekst bij de melding (b.v. schijf vol) | De worker probeert het opnieuw na 1, 2, … tot 6 uur. Los de oorzaak op; de melding verdwijnt na de volgende geslaagde back-up |
| Melding *Automatische back-up kan niet* | De opgeslagen wachtwoordzin past niet bij de hoofdsleutel (herstel op een nieuwe NAS, sleutel vervangen) | Stel de wachtwoordzin opnieuw in bij **Systeem › Back-ups en bewaren**, of gebruik `shc-api rekey --old-key-file` |
| *Back-up*: *Zigbee in laatste back-up: nee* | De app kan de Zigbee2MQTT-map niet lezen | Voer `install.sh` opnieuw uit (zet de rechten) en maak een nieuwe back-up |
| **Systeem › Meldingen buiten de app**: *Opgeslagen push-adres onbruikbaar* | Het adres is met een andere hoofdsleutel versleuteld | Vul het adres opnieuw in, of kies *Adres verwijderen* |
| Testbericht komt niet aan | Adres fout of ntfy niet bereikbaar | Controleer het onderwerp in de ntfy-app; de foutmelding staat bij *Testbericht sturen* |
| *Tijdsynchronisatie* blijft *onbekend* (*Tijdserver … niet bereikbaar*) of waarschuwt | De worker kan `pool.ntp.org` (UDP 123) niet bereiken, of de klok van de NAS wijkt af | Sta uitgaand NTP toe in je router en zet NTP aan op de NAS, of gebruik een eigen tijdserver (bijvoorbeeld je router): `--ntp-server <adres>` bij `render_truenas.py`, of `SHC_NTP_SERVER` bij docker compose. De rest van de app werkt door |
| *Vrije schijfruimte* oranje | Minder dan 15 % vrij | Ruim oude back-ups en `pre-migrate-…`-dumps in `<root>/backups/` op, of verkort de bewaartermijnen |

## Apparaten

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| Hue: *link-knop niet ingedrukt* | Normaal bij koppelen | Druk op de knop op de bridge en klik binnen 30 s op Koppelen |
| Hue: *certificaat gewijzigd* (`certificate_mismatch`) | Bridge vervangen of gereset | Koppel de bridge opnieuw; de bestaande koppeling wordt bijgewerkt |
| *Sleutel past niet, koppel opnieuw* | De apparaatsleutel is met een andere hoofdsleutel versleuteld | `shc-api rekey --old-key-file` met de oude sleutel, of koppel het apparaat opnieuw (zie [onderhoud.md](onderhoud.md#hoofdsleutel-vervangen)) |
| HomeWizard: *Lokale API uit* | Instelling in de Energy-app | Energy-app › apparaat › Meter-instellingen › Lokale API aan |
| HomeWizard: *apparaat op ander adres* (`identity_mismatch`) | Een ander HomeWizard-apparaat antwoordt op het oude IP (DHCP-wissel) | Pas het adres aan bij het apparaat op **Apparaten** (de app controleert het serienummer) en geef het een vast IP |
| Gree: *niet ondersteund* of geen antwoord | Model/firmware zonder lokaal protocol, of unit op 5 GHz/ander VLAN | Controleer 2,4 GHz-wifi via Gree+; zie de diagnose bij het apparaat (kopieerbaar). Gebruik de afstandsbediening; de rest van de app werkt door |
| Zoeken vindt niets | Containers zien geen broadcast/mDNS | Vul het IP-adres in; geef apparaten een vast IP in de router |
| Koppelen: *Dit adres hoort bij de app zelf* of *niet toegestaan* | Adres in de interne netwerken, loopback of buiten het thuisnetwerk | Gebruik het IP-adres van het apparaat in je thuisnetwerk; uitzonderingen in `SHC_PAIRING_ALLOWED_HOSTS` |
| Zigbee2MQTT start niet | Dongle niet gevonden of verkeerd pad | `ls -l /dev/serial/by-id/` en gebruik dat pad; controleer `adapter` (ember/zstack) in `configuration.yaml` |
| *Zigbee – USB-dongle* rood, *MQTT-verbinding* groen | Zigbee2MQTT draait niet (dongle los) | Steek de dongle terug; Zigbee2MQTT herstart en de status wordt vanzelf groen |
| Sensor *verouderd* | Batterij leeg of bereik | Batterij vervangen; dongle met verlengkabel weg van USB 3; een netgevoed Zigbee-apparaat als repeater |
| Waakhond: *<zone>: temperatuur reageert niet* | De kamer komt niet dichter bij het doel (raam open, airco in verkeerde modus of defect) | Controleer de unit en de kamer. De melding verdwijnt als de temperatuur weer reageert; zet de waakhond bij de zone eventueel langer of uit |
| Commando blijft *Verzonden · wacht op bevestiging* en wordt *Mislukt* | De unit liet de gevraagde stand niet zien | Kijk bij de zone in het logboek; probeer het opnieuw of gebruik de afstandsbediening |
| *Kies eerst een modus* | De airco staat uit | Kies een modus (verwarmen, koelen …); alleen temperatuur wijzigen zet een uitgeschakelde unit niet aan |
| *Veilige modus* | Na herstel/verhuizing | Controleer **Apparaten** en kies in **Systeem** *Apparaten kloppen — veilige modus opheffen* |

## Energie

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| Dag of uur met *geschat* / *ontbrekende metingen* | Meter (tijdelijk) offline | Normaal; totalen komen uit de tellerstanden en worden juist zodra er weer metingen zijn |
| *verdachte meterstand overgeslagen* | Een sprong groter dan het maximale vermogen van die rol | Klopt het maximum niet (b.v. zwaardere aansluiting)? Pas het aan bij **Energie › Meetgrenzen** |
| *Overig* is *geschat* | Een tussenmeter (airco of boiler) mist metingen | Controleer die meter op **Apparaten** |
| Kosten *gedeeltelijk* | Niet de hele periode valt onder een tarief | Voeg een tarief toe met een eerdere ingangsdatum |

## Inloggen en telefoons

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| *Te veel mislukte pogingen* | 5 keer een fout wachtwoord voor deze naam, of te veel pogingen vanaf dit adres | Wacht (1 minuut, oplopend tot 15). Een andere beheerder kan je ontgrendelen bij **Account › Gebruikers** |
| Wachtwoord vergeten | – | `docker exec -it <api-container> shc-api reset-password <gebruikersnaam>` |
| Telefoon: *Dit kan alleen in de web-app* | Gebruikers, telefoons, back-ups, herstellen, adressen en push-adres zijn alleen voor de web-app met wachtwoord | Doe het in de web-app |
| Android: QR-code ongeldig | Code verlopen (10 min) of al gebruikt | Maak een nieuwe code bij **Account › Telefoons** |
| Android: *Dit is niet jouw server* | Op dit adres antwoordt een ander apparaat, of de server is ouder dan de app | Controleer het wifi-netwerk. Werk eerst de server bij en daarna de app; via de tunnel blijft de app werken |
| Android: *Deze telefoon is niet meer gekoppeld* | Ingetrokken, verlopen (180 dagen) of onbekend na een herstel | Koppel opnieuw met een QR-code via **Account › Telefoons** |
| Android: *Koppel deze telefoon opnieuw, zodat de app je server kan herkennen…* | Gekoppeld met een oudere app en geen tunnel ingesteld | Koppel opnieuw |
| Android: *Onderweg* werkt niet | Tunnel/Access | Controleer de Access service token in **Meer › Cloudflare Access** en de public hostname naar `web:8080` |
| Web-app via de tunnel toont *Geen verbinding* terwijl internet werkt | De Cloudflare Access-sessie is verlopen | Herlaad de pagina en log opnieuw in bij Cloudflare (zie [buiten-huis.md](buiten-huis.md#cloudflare-access-sessie)) |

## Herstellen

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| *Deze back-up is gemaakt met een nieuwere versie* | De back-up komt van een nieuwere app | Werk de app eerst bij naar die versie of nieuwer |
| `restore`: *de worker draait nog* | Herstellen met de opdrachtregel terwijl de worker werkt | `shc-api maintenance on` of stop de worker-container |
| *N apparaatsleutel(s) passen niet bij de hoofdsleutel* | Back-up van een server met een andere `secret_key` | Zet na het herstel de oude sleutel om met `shc-api rekey --old-key-file`, of koppel die apparaten opnieuw |
| *Er loopt nog een back-up* | Herstel tijdens een back-up | Wacht tot de back-up klaar is |
