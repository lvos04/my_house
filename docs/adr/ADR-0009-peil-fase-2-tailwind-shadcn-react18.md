# ADR-0009 — Peil fase 2: Tailwind 4 + shadcn/ui (React 18) met recepten bij de haakklassen

- **Status:** aangenomen (2026-11-09, stap 3: web-UI naar Peil)
- **Context-ID's:** geen eigen Excel-ID (uiterlijk); ADR-0008; `docs/ontwerp/PEIL_MIGRATIE.md` §8.2, §9; Peil 1.1.0
  (`design/ontwerptaal/`); besluiten van de gebruiker O-09, O-11 en "my_house fase 2 op Tailwind 4 + shadcn/ui in de
  React 18-variant (forwardRef)".

## Context

Fase 1 (aliaslaag na `styles.css`) zette alle kleuren, radii en de focusring op Peil zonder één klasse te
veranderen. Fase 2 moest Tailwind 4, shadcn/ui en lucide invoeren, de statusvorm (icoon + woord + vorm) en de
toegestane toevoegingen bouwen, terwijl functies, routes, API-aanroepen, testids, rollen en de OpenAPI gelijk blijven.
De web-app heeft ± 150 plekken met `className="btn …"`, 211 inline styles, 25 unit-testbestanden en 11 e2e-scenario's
die op haakklassen, rollen en toegankelijke namen leunen.

## Besluit

1. **Eén CSS-ingang met vaste lagen** (`src/peil/peil.css`): `theme · base · legacy · components · utilities`.
   De bestaande CSS (`styles.css` en de vier feature-bestanden) en de aliaslaag staan in `legacy`; de Peil-recepten in
   `components`; Tailwind-utilities winnen van alles. De variabelen van `ontwerptaal.css` staan buiten een laag en
   winnen van de aliaslaag. De twee eigen kleurblokken van `styles.css` zijn weg; `var(--muted)` (oude tekstkleur)
   is overal `var(--muted-foreground)` geworden, omdat `--muted` in Peil een vlakkleur is.
2. **Geen Tailwind-preflight.** Alleen `tailwindcss/theme.css` en `tailwindcss/utilities.css`. De bestaande markup
   leunt op de standaardmarges van `p`, `ul`, `ol` en op inline SVG's; preflight zou de opbouw veranderen, niet alleen
   het uiterlijk. De Peil-basislaag uit `ontwerptaal.css` (lettertype, focusring, links, scroll-padding, reduced
   motion, forced colors) staat er wel in. Preflight kan in fase 3 mee als de inline styles weg zijn.
3. **shadcn/ui in de React 18-variant**: `Button`, `Badge`, `Card` met `React.forwardRef` + `displayName`, cva-varianten
   met de Peil-namen (`primair · secundair · stil · gevaar · link`, `standaard · klein · icoon`), `asChild` via
   `@radix-ui/react-slot`. **Het uiterlijk staat één keer**, als recept bij de haakklassen (`.btn`, `.primary`, `.pill`,
   `.card`, `.note`, `.tabs`, `.nav-link`, …) in `src/peil/componenten.css` met `@apply` van Peil-utilities; de
   cva-varianten zetten die haakklassen. Zo zien `<Button>` en een bestaande `<button className="btn">` er precies gelijk
   uit, blijven de haakklassen voor tests en de aliaslaag bestaan, en hoeven de ± 150 bestaande knoppen niet in één keer
   herschreven te worden. Nieuwe Peil-componenten (StatusBadge, ErnstLabel, BeleidsSlot, ThemaKeuze, HubLink) gebruiken
   utilities direct.
4. **Status altijd icoon + vast woord + vorm** (StatusBadge C-03): Gezond · Beperkt · Storing · Onbekend · Niet
   ingesteld; meldingen krijgen een ErnstLabel (Info · Middel · Hoog); de boiler een BeleidsSlot zonder knop in de
   DOM. Groen alleen voor gemeten gezondheid: "Online", "Gekoppeld", "Klaar", "Gelukt", "Loopt" en meetkwaliteit zijn
   neutraal. Iconen zijn lucide (`lucide-react` 1.70.0); in CSS-recepten (meldingen, readback, uitklapblokken) dezelfde
   lucide-paden als masker in de tekstkleur (CSP: `img-src 'self' data:` staat dat toe).
5. **Wat bewust níet naar Radix gaat:** native `<select>`, checkbox en radio, het eigen `Tabs.tsx` (toetsenbordlogica,
   ids) en de focusval van de Meer-sheet krijgen alleen de Peil-stijl. Uitgeschakelde knoppen houden native `disabled`
   (zonder opacity: gestippelde rand + gedempte tekst), met de reden ernaast zoals nu; `aria-disabled` is fase 3.
   ThemaKeuze is een groep gewone knoppen met `aria-pressed` (Radix ToggleGroup "single" geeft `role=radio`).
6. **Breekpunt zijbalk 1024 px** (Peil `lg`) in plaats van 821 px; daaronder kopbalk + ondernavigatie.
7. **Lettertype en maten:** systeemfont (fontsource-pakketten Inter en Space Grotesk verwijderd; ze werden niet meer
   gebruikt), tekst ≥ 14 px overal, body 16 px, h1 24 px; bedienbare doelen ≥ 44 × 44 px (ook "Alle instellingen",
   uitklapblokken en het kruisje van een rol).
8. **Themaknop (O-09):** `public/thema.js` (extern, CSP `script-src 'self'`) zet vóór de eerste weergave altijd het
   opgeloste thema (`data-theme` + `.dark` + `color-scheme` + `theme-color`); `src/theme.ts` doet hetzelfde na een
   keuze. Opslag `localStorage` `shc.thema` (Systeem = geen sleutel). Een unittest draait `thema.js` in jsdom en
   vergelijkt het met `theme.ts`.
9. **"Terug naar de Hub" (O-11):** de webcontainer schrijft bij de start uit de **optionele** omgevingsvariabele
   `HUB_URL` het bestand dat nginx als `/_shc/config.json` serveert (`apps/web/docker/15-shc-config.sh`, regels van
   contract 1.2 §3.2: alleen `http(s)://host[:poort]`, geen pad/query/fragment/inloggegevens, geen loopback,
   link-local, `0.0.0.0`, `::` of IPv4-gemapte adressen). Ongeldig of leeg = `{}`; de container start altijd. De app
   controleert het adres opnieuw (`src/hub.ts`) en toont de link alleen dan, in hetzelfde tabblad. Geen API-route,
   geen OpenAPI-wijziging, geen nieuw installatieveld in deze ronde.

## Afgewezen alternatieven

- **Alle knoppen nu naar `<Button>` herschrijven.** Groot, mechanisch en foutgevoelig zonder winst voor de gebruiker;
  het recept bij de haakklasse geeft hetzelfde uiterlijk. Komt in fase 3 samen met de inline styles.
- **Preflight laden en de verschillen met de hand terugzetten.** Verandert marges en lijstopmaak op tientallen
  plekken (geen uiterlijk maar opbouw) en is niet volledig te controleren met de bestaande tests.
- **Radix ToggleGroup voor de themaknop.** Geeft `role=radio`/`aria-checked`; Peil C-50 vraagt `aria-pressed`.
- **Hub-adres via `envsubst` in de nginx-template.** Een aanhalingsteken in `HUB_URL` zou de nginx-configuratie
  breken en de app laten uitvallen; het script valideert eerst en schrijft alleen een veilige origin.
- **Hub-adres uit de API, de URL of `document.referrer`.** Zie ADR-0008 (OpenAPI-wijziging, open-redirect-risico).

## Gevolgen

- OpenAPI byte-gelijk (controle in `docs/ontwerp/PEIL_MIGRATIE.md` §12); datamodel, routes, poorten en installatie
  ongewijzigd. De web-image accepteert één extra, optionele omgevingsvariabele (`HUB_URL`); zonder die variabele
  verandert er niets. Het veld in het Custom App-formulier hoort bij de alles-in-één image (ADR-0004, P3).
- Tailwind 4 vraagt Safari 16.4+, Chrome 111+, Firefox 128+ (PEIL_MIGRATIE §13 punt 8); welke wandpanelen/tablets
  in huis staan, is onbekend.
- Fase 3 (apart): inline styles → utilities, `styles.css` en de aliaslaag weg, preflight, `aria-disabled`.
