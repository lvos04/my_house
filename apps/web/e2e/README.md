# Browsertests (Playwright)

End-to-end tests in Chromium tegen de echte stack: PostgreSQL, de API (`shc-api serve`) en de gebouwde
web-app achter `vite preview`, die `/api` (ook de WebSocket) doorstuurt naar de API. Zonder worker en
zonder hardware: apparaten, knopkoppelingen, meldingen, metingen (een temperatuurreeks zoals de worker die opslaat) en (voor de offline-test) een hartslag van de achtergronddienst zet `seed.py` rechtstreeks in de testdatabase.

## Draaien

```sh
# vanuit de repository (PostgreSQL moet draaien; de rol moet databases mogen maken)
SHC_E2E_PG=postgresql://postgres@127.0.0.1:5432 apps/web/e2e/run-stack.sh
apps/web/e2e/run-stack.sh --trace off -g offline     # extra argumenten gaan naar `playwright test`
```

`run-stack.sh` maakt een nieuwe database, bouwt de web-app (`SHC_E2E_SKIP_BUILD=1` slaat dat over),
start API en `vite preview` op vrije poorten, draait de tests en ruimt alles op (`SHC_E2E_KEEP=1`
bewaart database en logboeken). De Python met `apps/api` en `integrations` komt uit `SHC_E2E_PYTHON`,
anders `apps/api/.venv/bin/python`, anders `python3`. Chromium installeer je met
`npx playwright install chromium`.

Rapporten en traces komen in `e2e/.output/` (niet in git). In CI draait de job `e2e` in
`.github/workflows/ci.yml`.

### Met het lettertype van de CI-machine

De web-app gebruikt het lettertype van het apparaat (`system-ui`). Op een ontwikkelmachine met Noto Sans is dat
smaller dan DejaVu Sans, waar de CI-machine op uitkomt. Een label dat lokaal net past, kan in CI buiten zijn vlak
steken (zo faalde CI-run 42981992176 op de waarde naast het eindpunt van de grafiek; ADR-0014 punt 5). Draai daarom
bij elke wijziging aan maten of labels ook een ronde met het bredere lettertype:

```sh
FONTCONFIG_FILE="$PWD/apps/web/e2e/ci-lettertype.conf" fc-match system-ui    # moet "DejaVu Sans" geven
FONTCONFIG_FILE="$PWD/apps/web/e2e/ci-lettertype.conf" apps/web/e2e/run-stack.sh
```

`ci-lettertype.conf` verbergt Noto Sans voor fontconfig (alleen voor dat ene commando; het systeem verandert niet).
Het werkt op Linux met fontconfig en met DejaVu Sans geïnstalleerd, en het pad moet volledig zijn. Het bootst
alleen het lettertype na, niet de rest van de CI-machine: de CI-run zelf blijft de maatstaf.

## Wat getest wordt

| Bestand | Scenario |
|---|---|
| `01-onboarding` | beheerder aanmaken, woning aanmaken (geen doodlopend scherm), thuisadres, eerste stappen installatiewizard |
| `02-climate` | airco koppelen, weekschema met sjabloon en dag kopiëren, opslaan, volgende wijziging; uitzetten → bediening uit, "Terug naar schema" |
| `03-energy` | tarief met lege optionele velden, vorige/volgende dag (periodetabs met tabpaneel), export (CSV en JSON) |
| `04-buttons` | Hue-knop met bridge-regel en twee koppelingen op één druk: conflict, simuleren, koppeling verwijderen |
| `05-meter-roles` | P1-meter rol geven, rol verplaatsen met bevestiging, loskoppelen |
| `06-backup` | automatische back-up instellen (ook dagelijks dump + wekelijks volledig), nu back-up maken, downloaden, controleren via upload; push-adres voor meldingen buiten de app weigeren, instellen, testen en verwijderen |
| `07-notifications` | melding verschijnt live (WebSocket), "Gezien", verdwijnt na oplossen; waakhondwaarschuwing bij de zone alleen zolang de waakhondmelding open is |
| `08-offline` | API stoppen → banner na de drempel, bediening uit, laatste gegevens zichtbaar; een gemeten "Gezond" (verse hartslag van de achtergronddienst als testgegevens) wordt "Verouderd", nooit groen; een nooit opgehaalde pagina is "Onbekend — nog niet opgehaald"; herstart → herstelt vanzelf |
| `09-viewer` | kijk-account ziet nergens bediening of beheer; de server weigert een commando |
| `10-mobile` | 360 en 320 px: knoppen minstens 44 × 44 px, thermostaat (−/+ en draaiknop) en kaarten blijven binnen hun kaart/de pagina, geen zijwaartse scroll, uitloggen via "Meer" |
| `11-peil` | ontwerptaal Peil: axe-core (WCAG 2.0/2.1/2.2 A+AA) = 0 op de hoofdschermen, de Meer-sheet en inloggen in licht en donker op 390 en 1440 px; Peil-matrix 320–1440 px (geen zijwaartse scroll, geen tekst < 14 px, doelen ≥ 44 px); themaknop (Donker blijft na herladen, Systeem volgt het apparaat); "Terug naar de Hub" alleen met een geldig adres uit `/_shc/config.json` (in de test nagespeeld met `page.route`) |
| `12-grafiek-24-uur` | grafiek "Laatste 24 uur" (Klimaat › Bediening) met zes uur metingen, een gat van 2½ uur en één meting zonder waarde: het plotvak is één Tab-stop met ring; → ← Home End Esc lezen per uur voor (`aria-live`) met een draadkruis; de muis kiest hetzelfde uur en "Toon als tabel" verspringt daarbij niet; "Toon als tabel, per uur" heeft dezelfde uren met "Geen meting" in het gat; axe-core = 0, de ring om het plotvak en de Peil-matrix op 320–1440 px in licht en donker met open tabel en gekozen uur; op een nagebootst aanraakscherm (390 px) kiest een tik een uur en blijft de keuze staan tot een tik ernaast; een veeg over de grafiek schuift de pagina en kiest geen uur, na slepen wist een tik ernaast de keuze, en knijpen met twee vingers op de grafiek zoomt de pagina in. Ontwerpreview (ADR-0014): het plotvak staat in een `<figure>` met bron en "bijgewerkt" eronder, de laatste meting is een eindpunt van 12 px (breed: met de waarde ernaast, in een strook die het label volgt), de tijdas heeft hele uren en loopt gelijk met het tekenvlak, en niets botst of steekt buiten het plotvak; een tijd in het bijschrift breekt nooit af en de tijdas staat minstens 6 px onder het plotvak, buiten de focusring; een airco die twee uur niets meldt en die de server verouderd noemt: de meetlijn en de doellijn stoppen, daarna een gearceerd stuk met stippelrand en "Verouderd sinds …" (als het past), de pil "Verouderd" met de laatst bekende waarde, "Geen meting" in uitlezing en tabel, axe-core = 0 in licht en donker op 390 en 1440 px. Met `SHC_E2E_SHOTS=<map>` bewaart de test afdrukken van de sectie |

`01-onboarding` draait als eerste (project `onboarding`); de andere tests bouwen voort op die
beheerder en woning en draaien na elkaar (één stack, en `08-offline` stopt de API).

`peil-metingen.ts` is geen testbestand: het bevat de metingen die `11-peil` en `12-grafiek-24-uur` delen (axe-core,
tekst < 14 px, doelen < 44 px, inhoud buiten zijn kaart of element, focusring).
