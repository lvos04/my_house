# Browsertests (Playwright)

End-to-end tests in Chromium tegen de echte stack: PostgreSQL, de API (`shc-api serve`) en de gebouwde
web-app achter `vite preview`, die `/api` (ook de WebSocket) doorstuurt naar de API. Zonder worker en
zonder hardware: apparaten, knopkoppelingen en meldingen zet `seed.py` rechtstreeks in de testdatabase.

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

## Wat getest wordt

| Bestand | Scenario |
|---|---|
| `01-onboarding` | beheerder aanmaken, woning aanmaken (geen doodlopend scherm), thuisadres, eerste stappen installatiewizard |
| `02-climate` | airco koppelen, weekschema met sjabloon en dag kopiëren, opslaan, volgende wijziging; uitzetten → bediening uit, "Terug naar schema" |
| `03-energy` | tarief met lege optionele velden, vorige/volgende dag, export (CSV en JSON) |
| `04-buttons` | Hue-knop met bridge-regel en twee koppelingen op één druk: conflict, simuleren, koppeling verwijderen |
| `05-meter-roles` | P1-meter rol geven, rol verplaatsen met bevestiging, loskoppelen |
| `06-backup` | automatische back-up instellen, nu back-up maken, downloaden, controleren via upload |
| `07-notifications` | melding verschijnt live (WebSocket), "Gezien", verdwijnt na oplossen |
| `08-offline` | API stoppen → banner na de drempel, bediening uit, laatste gegevens zichtbaar; herstart → herstelt vanzelf |
| `09-viewer` | kijk-account ziet nergens bediening of beheer; de server weigert een commando |
| `10-mobile` | 360 px: knoppen minstens 44 × 44 px, geen zijwaartse scroll, uitloggen via "Meer" |

`01-onboarding` draait als eerste (project `onboarding`); de andere tests bouwen voort op die
beheerder en woning en draaien na elkaar (één stack, en `08-offline` stopt de API).
