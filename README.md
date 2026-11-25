# Smart Home Control

Eén lokaal beheerde app voor je huis: Hue-verlichting en -knoppen, twee Gree-airco-units, HomeWizard-energiemeters en Zigbee-sensoren. Draait op TrueNAS SCALE als standaard Custom App met één image, werkt zonder Home Assistant en zonder cloud. Met een web-app (ook te installeren als PWA) en een Android-app.

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

- **TrueNAS SCALE:** de standaard Custom App met één image is de enige installatieweg. Wacht op de publicatie van de alles-in-één-image met vaste tag en digest. Vul daarna het formulier in. Bewaar vóór een update een snapshot van de hele dataset voor terugkeer naar de bijpassende versie. [Formuliervelden](deploy/truenas/CUSTOM_APP.md) · [Installatie en terugdraaien](docs/installatie-truenas.md)
- **Herstel oefenen:** open als beheerder **Systeem › Herstellen en verhuizen**. Daar controleer je een volledige export in een tijdelijke database en kun je na herstel de bewaarde oude hoofdsleutel inlezen. [Herstelproef](docs/onderhoud.md#herstel-oefenen-aanbevolen-elk-kwartaal) · [Sleutelbestand gebruiken](docs/onderhoud.md#hoofdsleutel-vervangen)
- **Android-app:** [apps/android/README.md](apps/android/README.md). De ondertekende APK staat bij de release zodra de Android-sleutel is ingesteld. De Android-workflow levert de testversie als artefact `huis-debug-apk`. [Releases](https://git.medialeon.nl/leon/my_house/releases) · [Forgejo Actions](https://git.medialeon.nl/leon/my_house/actions?workflow=android.yml)

Open de portal op de gekozen hostpoort (voorstel 8088), maak het beheerdersaccount en de woning aan en volg de **installatiewizard** (`/installatie`). Uitleg per fabrikant staat in de app op **Help** (`/help`). Zet daarna automatische back-ups aan bij **Systeem › Back-ups en bewaren**.

## Alleen voor ontwikkeling

Gebruik lokaal de ingang `deploy/dev/compose.yaml` voor de bestaande proefstack. Kopieer `.env.example` naar `.env` en zet `SHC_ROOT` op het absolute pad van je eigen voorbereide proefmap. Controleer de configuratie vanuit de repomap met `docker compose --env-file .env -f deploy/dev/compose.yaml config --quiet`. De hulpmiddelen `infra/scripts/install.sh` en `infra/scripts/render_truenas.py` ondersteunen de oude meercontainerproeven, waaronder ‘Install via YAML’. Voor de proefstack is er een apart [Cloudflare Tunnel-profiel](docs/buiten-huis.md).

## Hardware-advies (open punten)

- **Zigbee-dongle:** SONOFF ZBDongle-E (EFR32MG21, driver `ember`) of SMLIGHT SLZB-07. Gebruik een USB-verlengkabel van ±1 m, weg van USB 3-poorten en de NAS.
- **Sensoren:** Aqara WSDCGQ11LM of SONOFF SNZB-02D, beide ondersteund door Zigbee2MQTT.
- **Boiler (2000 W):** past op de Energy Socket (max. 3680 W). Laat een elektricien bevestigen dat de wandcontactdoos en groep geschikt zijn.
- **Airco-meting:** HomeWizard 1-fase kWh-meter (HWE-KWH1) op de aparte airco-groep, door een elektricien geplaatst.

## Repository

[Broncode op Forgejo](https://git.medialeon.nl/leon/my_house)

```
apps/api          FastAPI + worker + klimaat-/energie-engine (Python)
apps/web          React + TypeScript PWA
apps/android      Kotlin + Jetpack Compose app
integrations      Adapters: hue, gree, homewizard, zigbee (+ contracttests)
infra/compose     Compose-stack voor ontwikkeling en CI, broker- en Zigbee-config
deploy/truenas    velden van de standaard Custom App
deploy/manifest   platformmanifest van de alles-in-één-image
infra/scripts     hulpmiddelen, rooktest en browsertests voor de proefstack
infra/e2e         Browsertests tegen de echte images (nginx, API, worker, nep-airco)
docs              plan, API-contract, architectuur, beslissingen, handleidingen
```

## Documentatie

[Installatie op TrueNAS](docs/installatie-truenas.md) · [Architectuur](docs/architectuur.md) · [API-contract](docs/api.md) · [Onderhoud, back-up en verhuizen](docs/onderhoud.md) · [Problemen oplossen](docs/problemen-oplossen.md) · [Beveiliging](SECURITY.md) · [Testlog](docs/testlog.md) · [Compatibiliteit](docs/compatibility.md) · [Verbeterlijst en status](docs/verbeterlijst.md)

## Ontwikkelen

- Backend: `apps/api` en `integrations` (Python 3.12). Tests: `pytest` in beide mappen; de API-tests hebben PostgreSQL 16 nodig (`SHC_TEST_PG`). Pakketten komen uit het lock-bestand met hashes `apps/api/requirements.txt`; hoe je dat bijwerkt staat in [onderhoud.md](docs/onderhoud.md#afhankelijkheden-bijwerken-voor-wie-de-code-bijhoudt).
- Web: `apps/web` (`npm ci`, `npm test`); browsertests met `apps/web/e2e/run-stack.sh`, zie [apps/web/e2e/README.md](apps/web/e2e/README.md).
- Android: `apps/android` (`./gradlew :core:test :app:testDebugUnitTest`).
- Forgejo Actions draait bij pushes en pull requests de backend- en webtests, de versiewissels, de herstelproef en de browsertests. Bij Android-wijzigingen bouwt de Android-workflow ook een debug-APK. De handmatige releaseworkflow herhaalt de CI en de Android-unittests. [CI-runs](https://git.medialeon.nl/leon/my_house/actions?workflow=ci.yml) · [Android-runs](https://git.medialeon.nl/leon/my_house/actions?workflow=android.yml)

### Herstelproef met twee lokale Custom Apps

De CI-job **Custom App-rechten en migratiebewijs** start twee exemplaren van hetzelfde image met afzonderlijke datavolumes op een intern Docker-netwerk. A bevat twee proefaccounts, een woning met twee zones en een gesimuleerde meter met een rolbinding en meetgeschiedenis. B begint met alleen de tijdelijke beheerder.

Gebruik op een Linux-ontwikkelmachine het lokaal gebouwde image uit `deploy/allinone/Dockerfile`. Zet `SHC_AIO_IMAGE` op de eigen imagetag. Gebruik voor `SHC_STACK_LOCK` het gedeelde bestand `.lokaal/stack.lock` in de map waarin de repositories staan. Voer vanuit de repomap hetzelfde commando uit als in CI.

```bash
flock "$SHC_STACK_LOCK" nice -n 10 python3 infra/tests/allinone_herstelproef.py "$SHC_AIO_IMAGE"
```

De proef schrijft de gemeten controles naar [het testlog](docs/testlog.md). CI bewaart dit bestand als artifact `herstelproef-a-naar-b`.

## Platform (TrueNAS Home Platform V2)

my_house is een bestaande app van het platform: hij houdt zijn eigen routes, database en UI en krijgt alleen de hiaten
uit [docs/platform-v2/DELTA_MATRIX.md](docs/platform-v2/DELTA_MATRIX.md). Het platformcontract staat als byte-gelijke
kopie in [docs/platform-v2/PLATFORM_CONTRACTEN.md](docs/platform-v2/PLATFORM_CONTRACTEN.md) (`contracts 1.3`, met
`CONTRACT_1.3_CONFORMITEIT.md` en een drifttest: `python3 tools/contract_kopie.py controleer`); wat 1.3 van my_house
vraagt en wanneer, staat in §8 van de conformiteitslijst, de stand per punt in `docs/platform-v2/EVIDENCE.json`.
`HUB_URL` is in my_house alleen het adres achter "Terug naar de Hub": geen koppeling met de Hub, geen token (ADR-0012).

## Licentie

Licentie: **GPL-3.0** (zie [`LICENSE`](LICENSE); besluit van de eigenaar, 2026-11-09).
