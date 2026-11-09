# ADR-0008 — Omzetting naar de ontwerptaal Peil in twee fasen, met twee toegestane toevoegingen

- **Status:** aangenomen (2026-11-09): fase 1, fase 2 en beide toevoegingen gebouwd in de web-app; uitwerking en
  afwijkingen in [ADR-0009](ADR-0009-peil-fase-2-tailwind-shadcn-react18.md). De Android-app (punten 5 en 6) volgt nog.
- **Context-ID's:** geen eigen Excel-ID (uiterlijk); contract 1.2 §13 (ontwerptaal) en §2.3 punt 5 (uiterlijk van de
  bestaande apps); Peil 1.1.0 (`design/ontwerptaal/`), ONTWERPTAAL §13.5; besluiten van de gebruiker O-09, O-11,
  "my_house fase 2 op Tailwind 4 + shadcn in de React 18-variant", "ook de Android-apps".
- **Uitwerking:** [`docs/ontwerp/PEIL_MIGRATIE.md`](../ontwerp/PEIL_MIGRATIE.md)

## Context

my_house heeft een eigen ontwerptaal ("Rustig huis": warme lichte ondergrond, eigen CSS, Inter/Space Grotesk, 18 eigen
iconen) in de web-app en een eigen Compose-thema in de Android-app. Het platform kiest één ontwerptaal, Peil, op
Tailwind 4 + shadcn/ui + lucide. Voor de bestaande apps mag alleen het uiterlijk veranderen; functies, routes, API,
datamodel, poorten en installatie blijven, en de OpenAPI moet byte-gelijk blijven. De nulmeting (afdrukken in
`docs/screenshots/peil/voor/`, axe, contrast) laat zien: randen 1,3–1,8:1, groen voor dingen die geen gezondheid zijn,
dimmen met opacity, status als stip zonder woord, verwarmen en boiler in dezelfde oranje (ΔE 2,8 in donker), tekst van
11–13 px.

## Besluit

1. **Twee fasen voor de web-app.** Fase 1 = de gegenereerde aliaslaag na `styles.css` (alleen CSS-waarden en een paar
   selectorcorrecties; geen klasse, testid of tekst verandert). Fase 2 = Tailwind 4 + shadcn/ui (new-york, Peil-codemod)
   **in de React 18-variant**: elk gekopieerd component krijgt `React.forwardRef` + `displayName`. Geen React 19-upgrade.
   Oude CSS komt in `@layer legacy` onder de utilities. Fase 3 (inline styles weg, `aria-disabled`, oude CSS en
   fontsource weg) is een aparte, latere stap.
2. **Wat in fase 2 bewust níet naar Radix gaat:** native `<select>`, checkbox en radio (e2e gebruikt `selectOption`/
   `check()`, en native is beter op telefoons), het eigen `Tabs.tsx` (toetsenbordlogica en ids) en de focusval van de
   Meer-sheet. Die krijgen alleen de Peil-stijl. Uitgeschakelde knoppen houden in fase 2 native `disabled`
   (gedrag en 26 asserties blijven), met de reden zichtbaar ernaast.
3. **"Terug naar de Hub" (O-11):** het adres komt uit de optionele omgevingsvariabele `HUB_URL` (dezelfde naam en
   validatie als contract 1.2 §3.2), door de webserver als `/_shc/config.json` aangeboden. Leeg, afwezig of ongeldig
   = link verborgen. Nooit uit de URL, `document.referrer` of invoer van de gebruiker. Zelfde tabblad.
4. **Themaknop Systeem / Licht / Donker (O-09):** `localStorage` op het apparaat; een extern `public/thema.js` (CSP
   `script-src 'self'`, dus geen inline script) zet vóór de eerste paint altijd het opgeloste thema (`data-theme` +
   klasse `dark`). Geen serverinstelling.
5. **Android:** `HuisColors`/`MaterialTheme` krijgen hun waarden uit `tokens.json` (gegenereerd `PeilTokens.kt` + een
   JVM-test tegen drift); status altijd icoon + woord + vorm; 48 dp aanraakdoelen; licht/donker volgt het systeem.
   Geen themaknop en geen Hub-link in de Android-app (niet besloten).
6. **Iconen op Android:** de gebruikte lucide-iconen als vector drawables (ISC-licentie mee), geen nieuwe bibliotheek
   en geen Material Symbols, zodat web en telefoon hetzelfde beeld hebben.
7. **Peil-kopie in deze repo:** `design/ontwerptaal/` is een versie-gepinde kopie (1.1.0) met herkomstkop en sha256
   (`HERKOMST.md`); wijzigen alleen door een nieuwe versie te kopiëren.

## Afgewezen alternatieven

- **Alles in één keer naar Tailwind/shadcn.** Te veel tegelijk (± 600 regels CSS, 211 inline styles, 23 testbestanden en 10 e2e-scenario's);
  fase 1 levert zonder risico al het grootste deel van het contrast- en kleurherstel.
- **React 19-upgrade om de shadcn-componenten ongewijzigd te gebruiken.** Functioneel risico (React Query, router,
  testbibliotheek, PWA-plugin) zonder winst voor de gebruiker; de gebruiker koos de React 18-variant.
- **Hub-adres als instelling in de API (`/system/settings`).** Verandert de OpenAPI en het datamodel; niet toegestaan in
  deze ronde.
- **Hub-adres uit `document.referrer` of een queryparameter.** Onbetrouwbaar en een open-redirect-risico.
- **Material Symbols op Android.** Twee beeldtalen voor hetzelfde begrip (Peil §1 principe 8).
- **Paparazzi nu al in de repo** voor de Android-afdrukken. Dat is een buildwijziging; deze stap mocht niets aan de app
  bouwen. De nulmeting is daarom in een kopie buiten de repo gemaakt; Paparazzi in de repo hoort bij fase 2.

## Gevolgen

- Fase 1 is klein en omkeerbaar (één import). Fase 2 wijzigt per component, met afdrukken en tests per stap.
- De OpenAPI blijft byte-gelijk; controle in elke fase (commando in PEIL_MIGRATIE §12).
- Het Custom App-formulier krijgt één optioneel veld (`HUB_URL`); daarom gemeld als contractpunt.
- Aanvullingen op de aliaslaag (lichttegelrand, selectie-aanduiding, `.note.ok`, opacity, 14 px) gaan naar de
  generator van Peil (1.1.1), niet met de hand in de kopie.
