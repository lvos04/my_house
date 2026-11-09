# Testlog

Wat is getest, hoe, en wat (nog) niet. Bijgewerkt: 9 november 2026, na de verbeterronde (versie 0.1.0
in `VERSION`).

## Automatisch

### Uitkomst van de laatste run (deze omgeving)

| Suite | Uitkomst | Hoe gedraaid |
|---|---|---|
| API, worker, engine en migraties (`apps/api/tests`) | **311 geslaagd** | `pytest` tegen een echte PostgreSQL 16 |
| Adapters (`integrations/tests`) | **188 geslaagd, 1 overgeslagen** | `pytest`; de overgeslagen test is een contracttest die niet van toepassing is (*geen readback om te vervalsen*) |
| Web-app (`apps/web/src/test`) | **158 geslaagd** in 23 bestanden | `vitest run` (Testing Library, jsdom) |
| Browsertests (`apps/web/e2e`) | 13 tests in 10 bestanden; hier **niet gedraaid** | Chromium kan in deze omgeving niet worden gedownload. Ze draaien in CI (job `e2e`) |
| Browsertests tegen de images (`infra/e2e`) | 1 eigen test + een deel van de flows hierboven; hier **niet gedraaid** | Heeft Docker en de gebouwde images nodig. Draait in CI (job `stack`) |
| Android `:core` | 81 tests in 7 bestanden; hier **niet gedraaid** | Gradle kan hier geen plug-ins downloaden. Draait in GitHub Actions (workflow *Android* en elke release) |
| Android `:app` (unit-tests) | 27 tests in 2 bestanden; hier **niet gedraaid** | Heeft de Android SDK nodig. Draait in GitHub Actions |

### Wat de suites dekken

| Onderdeel | Tests | Bestanden |
|---|---|---|
| Klimaatregelaar, energie, beleid, adaptief (pure functies) | 26 | `apps/api/tests/test_core_logic.py` |
| API + database + migraties (basis) | 23 | `test_api.py`, `test_foundation.py` |
| Klimaat: engine, API en **worker met database** | 47 | `test_ws_climate_engine.py`, `test_ws_climate_api.py`, `test_ws_climate_worker.py` |
| Energie: uurtotalen, opruimen, kosten, export | 21 | `test_ws_energy_aggregates.py` |
| Back-up en herstel: bestandsformaat, API, herstel, hersteloefening | 39 | `test_ws_backup_format.py`, `test_ws_backup_api.py`, `test_ws_backup_restore.py` |
| Apparaten en koppelingen: kern, API, worker | 27 | `test_ws_devices_core.py`, `test_ws_devices_api.py`, `test_ws_devices_worker.py` |
| Beveiliging: inloggen, accounts, koppelen, WebSocket | 62 | `test_ws_security_auth.py`, `test_ws_security_accounts.py`, `test_ws_security_pairing.py`, `test_ws_security_ws.py` |
| **Storingsscenario's** | 6 | `test_ws_failures.py` |
| Vervolgpunten en laatste gaten (sleutels, telefoons, meldingen, documentatie) | 60 | `test_ws_followups.py`, `test_gapfix_*.py` |
| Adapters: Hue, HomeWizard, Gree, Zigbee, TLS, netwerkfouten | 137 | `integrations/tests/test_*.py` (behalve de contracttests) |
| **Contracttests per adapter** (dezelfde vaste set voor elke adapter) | 52 | `integrations/tests/contract_kit.py` + `test_contract_{gree,homewizard,hue,zigbee}.py` |
| Web-app: pagina's, rechten, offline, energie, klimaat, back-up/herstel, toegankelijkheid | 158 | `apps/web/src/test/*.test.ts(x)` |
| Android-kern: netwerk/adresregels, serverherkenning, API-client, commando's, schema, realtime | 81 | `apps/android/core/src/test` |
| Android-app: presentatie en teksten (`CoreTexts` tegen `strings.xml`) | 27 | `apps/android/app/src/test` |

**Worker met database** (`test_ws_climate_worker.py`): de echte worker-lussen met nep-adapters tegen
PostgreSQL. Scenario's: twee zones op één buitenunit worden samen besloten; een offline of op *auto*
staande unit blokkeert nog steeds een tegengestelde modus; een mislukt commando wordt opnieuw geprobeerd,
geeft een melding en verandert de regelstand niet; een onbevestigd commando wordt bij een latere
uitlezing alsnog bevestigd; een afstandsbediening-override loopt af en gaat terug naar schema zonder
lus; een schemablok zonder modus zet een uitgeschakelde unit nooit aan; alleen temperatuur wijzigen bij
een uitgeschakelde unit wordt geweigerd; het logboek groeit alleen bij een andere beslissing; de
waakhond meldt een kamer die niet opwarmt, ook met alleen de sensor van de unit.

**Storingsscenario's** (`test_ws_failures.py`): de databaseverbinding valt weg (de worker-lus gaat door
en herstelt), de MQTT-verbinding valt weg en komt terug, de Zigbee-bridge gaat offline (melding over de
dongle), een apparaat gaat offline en komt terug (met demping, geen knipperende status).

**Contracttests** (`integrations/tests/contract_kit.py`): elke adapter moet dezelfde vaste set halen
(mogelijkheden opgegeven, gebeurtenissen met tijdzone, gezondheid nooit "ok" zonder koppeling, een
niet-ondersteunde actie geeft `unsupported`, een onbereikbaar apparaat geeft een fout, een commando is
alleen bevestigd als de uitlezing het laat zien). Ze draaien tegen nep-apparaten die het echte protocol
spreken op 127.0.0.1: UDP voor Gree, HTTP voor HomeWizard, HTTPS met vastgezet certificaat voor Hue,
MQTT-berichten voor Zigbee.

### Browsertests (Playwright)

- **`apps/web/e2e`** (CI-job `e2e`, lokaal `apps/web/e2e/run-stack.sh`): Chromium tegen PostgreSQL, de
  echte API en de gebouwde web-app. Zonder worker en zonder hardware: apparaten zet `seed.py` in de
  testdatabase. 13 tests: eerste start en installatiewizard, klimaat en weekschema, energie en export,
  Hue-knoppen, meterrollen, back-up maken/downloaden/controleren en push-adres, meldingen live en de
  waakhondwaarschuwing, offline en herstel, kijk-account, en telefoonbreedte 360 en 320 px. Zie
  [apps/web/e2e/README.md](../apps/web/e2e/README.md).
- **`infra/scripts/e2e_compose.sh`** (CI-job `stack`): dezelfde flows, maar tegen de **echte images**
  achter nginx, met API, worker, PostgreSQL, mosquitto en een nep-Gree-airco op een testnetwerk, in een
  apart compose-project (`shc-e2e`). Standaard: eerste start, meldingen, offline, plus
  `infra/e2e/worker-command.e2e.ts`: de nep-airco koppelen, aan een zone hangen, de temperatuur verhogen
  en wachten tot de worker via de WebSocket *Bevestigd* meldt. Wegens de inlogrem van nginx (10 per
  minuut) draai je extra flows in kleine aparte runs (`SHC_E2E_FLOWS`).

### Overige controles in CI

- **Rooktest van de stack** (job `stack`): images bouwen, scannen met Trivy, `install.sh` op een verse
  map en de stack starten. Daarna via nginx: gezondheid, eerste start, woning aanmaken, de live-verbinding
  (WebSocket) op een andere poort, een back-up via de API, de grens op de grootte van verzoeken, de
  inlogrem en het tunnelpad. Dan `shc-api verify-backup` op die back-up, en controleren wat nginx
  antwoordt als de API stopt en dat alles daarna herstelt.
- **Compose** (job `compose`): `docker compose config` voor elk profiel en elke TrueNAS-render, en
  shellcheck op de scripts.
- **Audits:** pip-audit, npm audit en (wekelijks, `audit.yml`) Trivy op de uitgebrachte images.
- **Android** (workflow *Android* en elke release): `:core:test`, `:app:testDebugUnitTest` en de
  debug-APK.

### Scenario's uit plan §12 die automatisch gedekt zijn

- Boiler: alle routes (zone, lichtgroep, knop, regelaar, handmatig commando in de wachtrij) → geweigerd, ook als het commando al in de wachtrij stond (`test_boiler_socket_can_never_switch`, `test_boiler_never_switchable`).
- Twee units vragen verwarmen en koelen tegelijk → 409 met uitleg, ook vlak voor verzenden (`test_zone_command_policy_and_execution`, `test_shared_outdoor_conflict_blocks_cooling`, `test_two_zones_on_one_outdoor_unit_are_decided_together`).
- Afstandsbediening tijdens comfortregeling → automaat wacht; uitzetten wordt nooit automatisch teruggedraaid (`test_remote_control_change_becomes_override`, `test_remote_off_is_never_reactivated`, `test_remote_override_expires_back_to_schedule_without_loop`).
- Verouderde of onwaarschijnlijke sensor → geen bijsturing, OEM regelt (`test_stale_sensor_…`, `test_implausible_sensor`).
- Geen snel pendelen: minimum interval en max. starts per uur (`test_min_interval_respected`, `test_max_starts_prevents_rapid_cycling`).
- P1 tellerreset, uitschieters, dubbele en ontbrekende metingen, zomertijd 23/25 uur (`test_counter_reset_…`, `test_dst_day_has_25_hours`, `test_energy_summary_no_double_count_and_replacement`, `test_ws_energy_aggregates.py`).
- Meter vervangen halverwege de dag: historie blijft per meter, rol telt beide (`test_energy_summary_no_double_count_and_replacement`).
- Back-up: verkeerde wachtwoordzin en één gewijzigde byte → geweigerd; herstel → veilige modus blokkeert commando's; back-up van een nieuwere versie geweigerd, oudere bijgewerkt; hersteloefening in een tijdelijke database (`test_backup_corruption_refused_and_restore_safe_mode`, `test_ws_backup_restore.py`).
- Sleutels: back-up of instellingen met een andere hoofdsleutel → melding in plaats van een crash, en de herstelcontrole noemt het (`test_gapfix_keys.py`).
- Login en telefoons: geen standaardwachtwoord, CSRF, blokkade na mislukte pogingen, alleen-lezen rol, QR-koppeling eenmalig en intrekbaar, telefoonrollen, verlopen tokens, een beheer-telefoon mag geen gebruikers, koppelingen of back-ups beheren, serverherkenning met controlevraag, WebSocket sluit met 4401/4403 (`test_ws_security_*.py`, `test_gapfix_security.py`).
- Experimenten: "besparing" alleen bij vergelijkbare perioden, anders "indicatief" met redenen; automatisch wisselen van profielen en teruggeven aan de zone.

## Handmatige rooktest (eerste sessie)

API en worker als echte processen tegen PostgreSQL, met een nagebootste HomeWizard P1 op het netwerk:

- Beheerder aanmaken → woning → P1 koppelen via de API → rol *Huis totaal* → worker pollt elke 10 s → metingen in de database → live vermogen in de API → status *HomeWizard: Verbonden* → back-up maken → `verify-backup` toont alle tabellen.
- **Gevonden en opgelost:** (1) gekoppelde apparaten werden door de worker als nieuw apparaat op IP-adres gezien (instellingen van de adapter werden niet bewaard); (2) P1 met oudere firmware kon niet koppelen omdat alleen API v2 werd geprobeerd; (3) een validatiefout met eigen melding gaf 500 in plaats van 422. Voor alle drie staat er nu een test.

## Nog niet getest

- **Echte apparaten:** Hue Bridge (ook de knoppen en wat de bridge er zelf mee doet), Gree-units, HomeWizard P1, Energy Socket en kWh-meter, Zigbee-dongle en -sensoren. Status in de app: *nog niet getest*. Zie [compatibility.md](compatibility.md).
- **Gree:** of jouw modellen/firmware het lokale protocol ondersteunen (compatibiliteitsgate), ook nieuwere wifi-modules.
- **HomeWizard API v2:** of tijden echt zonder tijdzone binnenkomen, en het gedrag van de *Switch lock* van de Energy Socket.
- **TrueNAS:** installatie op TrueNAS 25.10, reboot, update en terugdraaien op je eigen NAS, en hoe het preset *Apps*/*SMB* (ACL's) zich gedraagt met `install.sh`.
- **Storingen op echte hardware:** USB-dongle eruit en terug, Hue Bridge zonder stroom, een herstart van mosquitto of PostgreSQL op de NAS, en of TrueNAS een gestopte worker-container echt opnieuw start. De code herstelt met backoff en de storingstests dekken dit met nep-onderdelen.
- **Uitgaand verkeer:** NTP-controle en push-berichten (ntfy) vanaf je eigen netwerk.
- **Android op een toestel:** QR-scanner (ook als de camera bezet of uitgeschakeld is), widget, meldingen op de achtergrond, wisselen tussen wifi en mobiel, TalkBack en grote letters. De `:app`-module compileert alleen in GitHub Actions.
- **Android-release:** de eigen release-sleutel aanmaken en als GitHub-geheim instellen, en controleren dat een nieuwe APK over de oude heen installeert (en dat de verkleinde R8-build werkt).
- **GitHub:** echte runs van `release.yml`, `edge.yml` en `audit.yml`, cosign-ondertekening en Dependabot-PR's.
- **Cloudflare:** een verlopen Access-sessie op een geïnstalleerde PWA, en split-horizon DNS met een eigen reverse proxy.

## Eerste test op je NAS (checklist)

1. Installeren volgens [installatie-truenas.md](installatie-truenas.md). De installatiewizard (`/installatie`) begint met *Controles vooraf*; **Systeem** toont alles groen behalve back-up.
2. Automatische back-ups aanzetten (**Systeem › Back-ups en bewaren**), een back-up maken, app herstarten, TrueNAS rebooten: inloggen werkt, gegevens zijn er nog.
3. Hue koppelen; een groep aan/uit vanuit de app; knop indrukken en de gebeurtenis zien; *Simuleren* bij een knopkoppeling.
4. HomeWizard P1 en socket koppelen, rollen toewijzen; na een uur een kloppend uurtotaal.
5. Gree: unit zoeken. Lukt binden en uitlezen? Dan één setpointwijziging en controleren dat de app *Bevestigd* toont.
6. Zigbee (na aankoop dongle): sensor koppelen, uit bereik halen → na 30 min *verouderd*; dongle eruit → *Zigbee – USB-dongle* rood.
7. Worker-container stoppen → binnen 3 minuten de melding *Achtergronddienst reageert niet* (en een push-bericht als dat is ingesteld).
8. Telefoon koppelen via **Account › Telefoons**, thuis en via de tunnel gebruiken, daarna intrekken: de app toont *Deze telefoon is niet meer gekoppeld*.
9. Herstel oefenen: `shc-api restore-drill` (zie [onderhoud.md](onderhoud.md#herstel-oefenen-aanbevolen-elk-kwartaal)).
