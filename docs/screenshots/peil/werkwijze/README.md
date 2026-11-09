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
| `fixtures.mjs` | JSON van de echte API (`overview`, `energy/summary`, `schedule`) voor de Android-afdrukken |
| `axe.mjs` | nulmeting axe-core 4.14 + eigen metingen → `axe-voor.json` |
| `review.mjs` | stap 5, onafhankelijke review (`PEIL_SCRIPT=review.mjs`): per scherm × thema × 320/360/390/768/1024/1440 axe-core 4.14, contrast in de gerenderde pagina (methode van Peil `bouw/controle.cjs`), UI-grenzen, doelen, overloop, inhoud buiten zijn element, koppen, groen-audit, "alleen kleur"-kandidaten, de zichtbare tekst voor de woordenlijst, op 390/1440 een volledige Tab-doorloop; afdrukken op 360/390/768/1440 → `review.json` + `shots/` |
| `analyse.py` | vat `review.json` samen (`python analyse.py review.json --json samenvatting.json`): tellingen, details, groen-audit, woordenlijst |
| `nabewerk.py` | Android-delen samenvoegen/inkorten en alle PNG's verkleinen (vlakkleuren exact, met controle) |
| `android/PeilVoorShots.kt` | Paparazzi-test voor de Android-afdrukken van de nulmeting (alleen in een kopie van `apps/android`) |
| `android/verzamel.py` | Android ná de omzetting: afdrukken uit het Paparazzi-rapport halen en de `-volledig`-delen samenvoegen |

**Vereist (deze machine, 9 nov 2026):** Python 3.12-venv met `pip install -e integrations -e apps/api` (pad staat
bovenin `run_shots.sh` als `PY`), PostgreSQL 16 op 127.0.0.1:5432 waarin de rol databases mag maken, een gebouwde
web-app (`npm run build`, of laat het script bouwen), Playwright met Chromium (pad bovenin de `.mjs`-bestanden),
axe-core (pad in `axe.mjs`), Pillow + numpy voor `nabewerk.py`. Pas de absolute paden aan je eigen opzet aan.

## Android (Paparazzi in een kopie)

1. Kopieer `apps/android` en het bestand `VERSION` naar een map buiten de repo, met dezelfde onderlinge ligging
   (`<map>/VERSION`, `<map>/apps/android`).
2. In de kopie: `build.gradle.kts` → `id("app.cash.paparazzi") version "1.3.5" apply false` in `plugins`;
   `app/build.gradle.kts` → `id("app.cash.paparazzi")` in `plugins` en
   `testImplementation("org.mockito:mockito-core:5.14.2")`.
3. Zet `android/PeilVoorShots.kt` in `app/src/test/java/nl/huis/smarthome/peil/` en de JSON van `fixtures.mjs` in
   `app/src/test/resources/peil/`.
4. `JAVA_HOME=<JDK 17> ANDROID_HOME=<sdk> flock …/gradle.lock ./gradlew --no-daemon --max-workers=4
   -Dorg.gradle.jvmargs=-Xmx2g :app:recordPaparazziDebug --tests 'nl.huis.smarthome.peil.*'`
5. Afdrukken staan in `app/src/test/snapshots/images/`; hernoemen naar `<scherm>-<thema>-<breedte>.png` en de drie
   delen van `-volledig` onder elkaar zetten (zie de README een map hoger), daarna `nabewerk.py`.

In fase 2 hoort Paparazzi als testafhankelijkheid in de repo zelf (zie `docs/ontwerp/PEIL_MIGRATIE.md` §12); dan
vervalt de kopie.

## Android ná de omzetting (stap 4, Paparazzi in de repo)

Sinds stap 4 staat de test in de repo (`apps/android/app/src/test/java/nl/huis/smarthome/peil/PeilAfdrukken.kt`,
ADR-0010); er is geen kopie meer nodig. Vanuit `apps/android` (JDK 17, Android SDK 35; op deze machine onder
`flock …/gradle.lock` met `--max-workers=4` en `-Dorg.gradle.jvmargs=-Xmx2g`):

```sh
./gradlew :app:recordPaparazziDebug                                        # referentiebeelden (56) vastleggen
./gradlew :app:verifyPaparazziDebug                                        # vergelijken (zo doet CI het)
PEIL_VOLLEDIG=1 ./gradlew :app:testDebugUnitTest --tests 'nl.huis.smarthome.peil.*'   # ook de lange delen
python android/verzamel.py ../../apps/android/app/build/reports/paparazzi/debug <ruwe map>
python nabewerk.py <ruwe map> ../android                                   # inkorten en verkleinen
```
