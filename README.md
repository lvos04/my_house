# Smart Home Control

Eén lokaal beheerde app voor je huis: Hue-verlichting en -knoppen, twee Gree-airco-units, HomeWizard-energiemeters en Zigbee-sensoren. Draait als één TrueNAS SCALE Custom App (of op elke Linux-server met Docker), werkt zonder Home Assistant en zonder cloud. Met een web-app (ook te installeren als PWA) en een Android-app.

> **Status:** software compleet voor fase 0–8 van het [implementatieplan](docs/implementatieplan.md), plus de verbeterronde uit de [verbeterlijst](docs/verbeterlijst.md). **Nog niet getest met jouw echte apparaten.** Elke adapter is getest tegen nep-apparaten die het echte protocol spreken, op basis van de officiële documentatie. Zie [testlog](docs/testlog.md) en [compatibiliteit](docs/compatibility.md).

## Wat kan het

| Onderdeel | Wat werkt | Eerst nodig bij de fabrikant |
|---|---|---|
| Philips Hue | Lampen, groepen, scènes, knoppen via lokale API v2 + eventstream | Lampen/knoppen in de Hue-app aan de bridge toevoegen; link-knop indrukken bij koppelen |
| Gree multisplit | Aan/uit, verwarmen/koelen, doeltemperatuur, ventilator, swing via lokaal UDP-protocol | Units via Gree+ op 2,4 GHz-wifi zetten. **Compatibiliteit per model nog onbekend** |
| HomeWizard | P1 (totaal, import/export, gas), Energy Socket (boiler, alleen meten), 1-fase kWh-meter (airco) | "Lokale API" aanzetten in de HomeWizard Energy-app |
| Zigbee | Temperatuur/vocht, batterij, signaal via Zigbee2MQTT | USB-dongle (advies hieronder) |

Verder: klimaatregeling met weekschema's, comfortprofielen, waakhond en veilige terugval; energie per dag/week/maand/jaar met kosten, gas en kwaliteitslabels; exports (CSV/JSON); meldingen in de app en optioneel als push-bericht (ntfy); automatische versleutelde back-ups, herstellen via de app en een verhuisassistent; gebruikers- en telefoonbeheer met rollen en een logboek.

**Bewuste grenzen:** de boiler wordt nooit geschakeld (de app blokkeert het op drie plekken; de HomeWizard-app zelf kan de stekker nog wel schakelen). Verbruik per binnenunit wordt niet gemeten: beide units delen één compressor, de kWh-meter meet ze samen.

## Ontwerp

De web-app en de Android-app volgen de ontwerptaal **Peil** 1.1.1 van het TrueNAS Home Platform (gepinde kopie met bron-commit en driftcontrole in [design/ontwerptaal/](design/ontwerptaal/HERKOMST.md)): licht en donker (in de web-app te kiezen: Systeem, Licht of Donker), een status altijd als icoon + woord + vorm (Gezond · Beperkt · Storing · Onbekend · Verouderd), groen alleen voor gemeten gezondheid, oranje = verwarmen, blauw = koelen, en elk getal met een kwaliteitslabel (gemeten · afgeleid · geschat · geen meting). Alleen het uiterlijk is veranderd; functies, API en installatie zijn gelijk gebleven. Ontwerp en uitvoering: [docs/ontwerp/PEIL_MIGRATIE.md](docs/ontwerp/PEIL_MIGRATIE.md), [docs/ontwerp/PEIL_REVIEW.md](docs/ontwerp/PEIL_REVIEW.md) en ADR-0008 tot en met ADR-0012 in [docs/adr/](docs/adr/). Het oudere ontwerp-canvas [Smart Home Control](https://claude.ai/artifact/9jVrAYrtre4K8BkM4rekav) ("Rustig huis") is daarmee vervangen.

## Snel starten

- **TrueNAS SCALE 25.10:** [docs/installatie-truenas.md](docs/installatie-truenas.md)
- **Andere Linux-server:** `sudo infra/scripts/install.sh /pad/naar/data` en daarna `SHC_ROOT=/pad/naar/data SHC_VERSION=$(cat VERSION) docker compose -f infra/compose/compose.yaml up -d` (Zigbee en tunnel: `--profile zigbee` / `--profile tunnel`).
- **Android-app:** [apps/android/README.md](apps/android/README.md). De ondertekende APK staat bij elke GitHub-release (zodra de release-sleutel is ingesteld); een testversie komt uit GitHub Actions (artifact `huis-debug-apk`).
- **Buiten huis (Cloudflare Tunnel):** [docs/buiten-huis.md](docs/buiten-huis.md)

Open daarna `http://<server>:8088`, maak het beheerdersaccount aan (er is geen standaardwachtwoord), maak de woning aan en volg de **installatiewizard** (`/installatie`). Uitleg per fabrikant staat in de app op **Help** (`/help`). Zet daarna automatische back-ups aan bij **Systeem › Back-ups en bewaren**.

## Hardware-advies (open punten)

- **Zigbee-dongle:** SONOFF ZBDongle-E (EFR32MG21, driver `ember`) of SMLIGHT SLZB-07. Gebruik een USB-verlengkabel van ±1 m, weg van USB 3-poorten en de NAS.
- **Sensoren:** Aqara WSDCGQ11LM of SONOFF SNZB-02D, beide ondersteund door Zigbee2MQTT.
- **Boiler (2000 W):** past op de Energy Socket (max. 3680 W). Laat een elektricien bevestigen dat de wandcontactdoos en groep geschikt zijn.
- **Airco-meting:** HomeWizard 1-fase kWh-meter (HWE-KWH1) op de aparte airco-groep, door een elektricien geplaatst.

## Repository

```
apps/api          FastAPI + worker + klimaat-/energie-engine (Python)
apps/web          React + TypeScript PWA
apps/android      Kotlin + Jetpack Compose app
integrations      Adapters: hue, gree, homewizard, zigbee (+ contracttests)
infra/compose     Compose-stack (TrueNAS / Linux), broker- en Zigbee-config
infra/scripts     install.sh, render_truenas.py, rooktest en browsertests tegen de stack
infra/e2e         Browsertests tegen de echte images (nginx, API, worker, nep-airco)
docs              plan, API-contract, architectuur, beslissingen, handleidingen
```

## Documentatie

[Installatie op TrueNAS](docs/installatie-truenas.md) · [Buiten huis](docs/buiten-huis.md) · [Architectuur](docs/architectuur.md) · [API-contract](docs/api.md) · [Onderhoud, back-up en verhuizen](docs/onderhoud.md) · [Problemen oplossen](docs/problemen-oplossen.md) · [Beveiliging](SECURITY.md) · [Testlog](docs/testlog.md) · [Compatibiliteit](docs/compatibility.md) · [Verbeterlijst en status](docs/verbeterlijst.md)

## Ontwikkelen

- Backend: `apps/api` en `integrations` (Python 3.12). Tests: `pytest` in beide mappen; de API-tests hebben PostgreSQL 16 nodig (`SHC_TEST_PG`). Pakketten komen uit het lock-bestand met hashes `apps/api/requirements.txt`; hoe je dat bijwerkt staat in [onderhoud.md](docs/onderhoud.md#afhankelijkheden-bijwerken-voor-wie-de-code-bijhoudt).
- Web: `apps/web` (`npm ci`, `npm test`); browsertests met `apps/web/e2e/run-stack.sh`, zie [apps/web/e2e/README.md](apps/web/e2e/README.md).
- Android: `apps/android` (`./gradlew :core:test :app:testDebugUnitTest`).
- CI (`.github/workflows/ci.yml`) draait de backend- en webtests bij elke push en pull request, plus een rooktest en browsertests tegen de gebouwde images. De Android-tests en de debug-APK komen uit `.github/workflows/android.yml` (bij wijzigingen in `apps/android`) en draaien bij elke release opnieuw.

## Platform (TrueNAS Home Platform V2)

my_house is een bestaande app van het platform: hij houdt zijn eigen routes, database en UI en krijgt alleen de hiaten
uit [docs/platform-v2/DELTA_MATRIX.md](docs/platform-v2/DELTA_MATRIX.md). Het platformcontract staat als byte-gelijke
kopie in [docs/platform-v2/PLATFORM_CONTRACTEN.md](docs/platform-v2/PLATFORM_CONTRACTEN.md) (`contracts 1.3`, met
`CONTRACT_1.3_CONFORMITEIT.md` en een drifttest: `python3 tools/contract_kopie.py controleer`); wat 1.3 van my_house
vraagt en wanneer, staat in §8 van de conformiteitslijst, de stand per punt in `docs/platform-v2/EVIDENCE.json`.
`HUB_URL` is in my_house alleen het adres achter "Terug naar de Hub": geen koppeling met de Hub, geen token (ADR-0012).

## Licentie

Licentie: **GPL-3.0** (zie [`LICENSE`](LICENSE); besluit van de eigenaar, 2026-11-09).
