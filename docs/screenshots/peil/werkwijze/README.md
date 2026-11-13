# Werkwijze: hulpscripts voor de Peil-afdrukken

Alleen documentatie: hiermee zijn de afdrukken in `../voor/` en `../na/` gemaakt (zelfde namen, zodat voor en na
naast elkaar te leggen zijn). Sinds stap 3 kent `shots.mjs` ook `PEIL_ALLEEN=<scherm>,…` (alleen die schermen),
`PEIL_THEMAS=licht|donker` en `PEIL_GEEN_HUB=1` (geen nagespeeld Hub-adres); `axe.mjs` schrijft naar
`PEIL_AXE_NAAM` (standaard `axe-voor.json`). CI gebruikt ze niet,
de app importeert ze niet. **Kopieer deze map naar een plek buiten de repo voordat je ze draait**: `run_shots.sh`
maakt een `tmp/`-map naast zichzelf.

| Bestand | Wat |
|---|---|
| `run_shots.sh` | maakt een wegwerp-database (`my_house_peil_*`), start de echte API en `vite preview` op vrije poorten (zoals `apps/web/e2e/run-stack.sh`) en draait daarna `shots.mjs` (of `PEIL_SCRIPT=…`); ruimt de database op |
| `seed_peil.py` | fictieve testgegevens rechtstreeks in de wegwerp-database (apparaten, metingen, rollen, tarief, meldingen); `stale` maakt metingen 3 uur oud; `heartbeat_loop` zet de worker-hartslag (er draait geen worker) |
| `shots.mjs` | alle web-afdrukken (28 schermen/toestanden × 360/390/768/1440 × licht/donker) + `index.json`; dekt echte LAN-adressen van de testmachine af |
| `testaccounts.mjs` | de fictieve testaccounts (beheerder en kijker) voor `shots.mjs`, `fixtures.mjs`, `axe.mjs` en `review.mjs`, gelezen uit `apps/web/e2e/helpers.ts` (`ADMIN` en `VIEWER` van de browsertests; de waarden staan alleen daar); `PEIL_REPO` zoals bij `run_shots.sh` |
| `fixtures.mjs` | JSON van de echte API (`overview`, `energy/summary`, `schedule`) voor de Android-afdrukken |
| `axe.mjs` | nulmeting axe-core 4.14 + eigen metingen → `axe-voor.json` |
| `review.mjs` | stap 5, onafhankelijke review (`PEIL_SCRIPT=review.mjs`): per scherm × thema × 320/360/390/768/1024/1440 axe-core 4.14, contrast in de gerenderde pagina (methode van Peil `bouw/controle.cjs`), UI-grenzen, doelen, overloop, inhoud buiten zijn element, koppen, groen-audit, "alleen kleur"-kandidaten, de zichtbare tekst voor de woordenlijst, op 390/1440 een volledige Tab-doorloop; afdrukken op 360/390/768/1440 → `review.json` + `shots/` |
| `analyse.py` | vat `review.json` samen (`python analyse.py review.json --json samenvatting.json`): tellingen, details, groen-audit, woordenlijst |
| `nabewerk.py` | Android-delen samenvoegen/inkorten en alle PNG's verkleinen (vlakkleuren exact, met controle) |
| `android/PeilVoorShots.kt` | Paparazzi-test voor de Android-afdrukken van de nulmeting (alleen in een kopie van `apps/android`) |
| `android/verzamel.py` | Android ná de omzetting: afdrukken uit het Paparazzi-rapport halen en de `-volledig`-delen samenvoegen |

**Vereist:** Python 3.12 met `pip install -e integrations -e apps/api` (`PEIL_PYTHON`, standaard `python3`),
PostgreSQL 16 waarin de rol databases mag maken (`PEIL_PG`, standaard `postgresql://postgres@127.0.0.1:5432`), een
gebouwde web-app (`npm run build`, of laat het script bouwen), Playwright met Chromium en axe-core (standaard die van
`apps/web` na `npm ci` en `npx playwright install chromium`; anders `PEIL_PLAYWRIGHT=<pad>/playwright/index.mjs` en
`PEIL_AXE=<pad>/axe-core/axe.min.js`), Pillow + numpy voor `nabewerk.py`. Na het kopiëren van deze map wijst
`PEIL_REPO` naar de repo:

```sh
cp -r docs/screenshots/peil/werkwijze /tmp/werkwijze
PEIL_REPO=<pad naar my_house> PEIL_PYTHON=<venv>/bin/python /tmp/werkwijze/run_shots.sh /tmp/afdrukken
```

Er staan geen absolute paden in de scripts; alles komt uit deze variabelen.

## Android (Paparazzi in een kopie)

1. Kopieer `apps/android` en het bestand `VERSION` naar een map buiten de repo, met dezelfde onderlinge ligging
   (`<map>/VERSION`, `<map>/apps/android`).
2. In de kopie: `build.gradle.kts` → `id("app.cash.paparazzi") version "1.3.5" apply false` in `plugins`;
   `app/build.gradle.kts` → `id("app.cash.paparazzi")` in `plugins` en
   `testImplementation("org.mockito:mockito-core:5.14.2")`.
3. Zet `android/PeilVoorShots.kt` in `app/src/test/java/nl/huis/smarthome/peil/` en de JSON van `fixtures.mjs` in
   `app/src/test/resources/peil/`.
4. `JAVA_HOME=<JDK 17> ANDROID_HOME=<sdk> ./gradlew --no-daemon :app:recordPaparazziDebug
   --tests 'nl.huis.smarthome.peil.*'` (met weinig geheugen eventueel `--max-workers=4 -Dorg.gradle.jvmargs=-Xmx2g`)
5. Afdrukken staan in `app/src/test/snapshots/images/`; hernoemen naar `<scherm>-<thema>-<breedte>.png` en de drie
   delen van `-volledig` onder elkaar zetten (zie de README een map hoger), daarna `nabewerk.py`.

In fase 2 hoort Paparazzi als testafhankelijkheid in de repo zelf (zie `docs/ontwerp/PEIL_MIGRATIE.md` §12); dan
vervalt de kopie.

## Android ná de omzetting (stap 4, Paparazzi in de repo)

Sinds stap 4 staat de test in de repo (`apps/android/app/src/test/java/nl/huis/smarthome/peil/PeilAfdrukken.kt`,
ADR-0010); er is geen kopie meer nodig. Vanuit `apps/android` (JDK 17, Android SDK 35; met weinig geheugen
eventueel `--max-workers=4` en `-Dorg.gradle.jvmargs=-Xmx2g`):

```sh
./gradlew :app:recordPaparazziDebug                                        # referentiebeelden (56) vastleggen
./gradlew :app:verifyPaparazziDebug                                        # vergelijken (zo doet CI het)
PEIL_VOLLEDIG=1 ./gradlew :app:testDebugUnitTest --tests 'nl.huis.smarthome.peil.*'   # ook de lange delen
python android/verzamel.py ../../apps/android/app/build/reports/paparazzi/debug <ruwe map>
python nabewerk.py <ruwe map> ../android                                   # inkorten en verkleinen
```
