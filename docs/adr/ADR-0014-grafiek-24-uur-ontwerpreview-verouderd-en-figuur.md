# ADR-0014 — Grafiek "Laatste 24 uur": ontwerpreview (verouderd, figuur met bron, eindpunt, tijdas)

- **Status:** aangenomen (2026-11-11)
- **Context-ID's:** geen eigen Excel-ID (uiterlijk, teksten en toegankelijkheid); onafhankelijke ontwerpreview van het
  werk uit [ADR-0013](ADR-0013-grafiek-24-uur-tabel-en-toetsenbord.md); Peil 1.1.1 §10 (grafieken: eerlijkheid,
  `<figure>` + `figcaption`, "Toon als tabel") en COMPONENTEN.md "Lijngrafiek" (`design/ontwerptaal/`); het
  Peil-prototype `server-metrieken.html`; ADR-0011 (verouderd en nooit opgehaald);
  [`docs/ontwerp/PEIL_REVIEW.md`](../ontwerp/PEIL_REVIEW.md) §12.

## Context

ADR-0013 gaf de grafiek een tabel per uur en bediening met het toetsenbord. Twee Peil-regels bleven daarbij bewust
liggen: "Verouderd: de lijn stopt bij het laatste punt; daarna arcering met 'Verouderd sinds 10:12'" en het eindpunt met
een direct label. De reden toen: de x-as liep tot het laatste punt, dus er was geen vlak ná het laatste punt.

De onafhankelijke review (80 combinaties van toestand × breedte × thema, gemeten in Chromium tegen de echte API) vond
geen overtreding van axe-core en geen contrastfout, maar wel dit:

1. **Een verouderde sensor zag eruit als een actuele.** Meldt de airco twee uur niets, dan noemt de server de sensor
   verouderd (`zone.temperature.stale`), maar de grafiek eindigde gewoon bij de laatste meting, zonder enig teken. De
   bovenste helft van het scherm zei "Verouderd", de grafiek eronder zei niets. Dat is geen kleurfout maar wel een
   eerlijkheidsfout (Peil §10; "unknown/stale nooit groen" geldt ook voor "ziet er gewoon goed uit").
2. **Geen `<figure>`, geen bron, geen tijd.** Peil vraagt `figcaption` met bron en tijd; de grafiek was een losse `div`.
3. **Geen eindpunt.** De laatste meting, het getal waar de lezer voor komt, was niet gemarkeerd.
4. **De tijdas had alleen een begin- en een eindtijd**, ook op 1440 px: tussen 08:31 en 08:29 was geen uur af te lezen
   zonder de aanwijzer of het toetsenbord.
5. **De y-labels staken 4,8 px boven en onder het plotvak uit**; de focusring van het plotvak liep daardoor door de
   bovenste en onderste graadaanduiding heen.
6. **De legenda stond ook boven een lege toestand** ("Nog geen geschiedenis") en had staaltjes van 12 px.

## Besluit

1. **Verouderd volgt het oordeel van de server, nooit een eigen rekensom.** De grafiek tekent de verouderd-markering
   alleen als `zone.temperature.stale` waar is. De app beslist niet zelf dat een reeks "te oud" is: de drempel
   (`policy.stale_after_s`) en de klok horen bij de server (zelfde lijn als ADR-0011).
   - De **meetlijn stopt** bij de laatste meting met waarde; daar staat het eindpunt.
   - Daarna volgt een **gearceerd stuk** tot het eind van de as (`.hist-oud`, zelfde `arcering-gat` als een gat), met
     links een **gestippelde rand** in `border-stale` (2 px, de vorm van de verouderd-pil). Ligt de laatste meting vóór
     het begin van de grafiek, dan is het hele plotvak gearceerd en vervalt de rand (er is geen overgang te tonen).
   - In het stuk staat **"Verouderd sinds 06:28"**, maar alleen als het past: vanaf een stuk van 9 rem (tussen 9 en
     ± 11 rem op twee regels, daarboven op één). Smaller: geen label — nooit afgekapt en nooit over de meetlijn, want
     het staat binnen het gearceerde stuk. De tijd staat dan nog steeds onder de grafiek en in de toegankelijke naam.
   - Onder de grafiek staat de **pil "Verouderd"** (`StatusBadge status="stale"`: icoon, woord en gestippelde vorm —
     dus niet alleen kleur) met "Laatst bekend 11 nov 06:28: 20,1°. Daarna kwam er geen meting meer."
   - De **toegankelijke naam** van het plotvak eindigt op ", verouderd sinds 11 nov 06:28"; de uren na de laatste
     meting heten in de leesregel en in de tabel "Geen meting" (het begrip uit ADR-0013).
   - Er staat **geen "bijgewerkt"** bij een verouderde sensor: "bijgewerkt 06:28" naast "Verouderd" leest als een
     tegenspraak. De regel wordt "Bron: sensor van de airco."
2. **De breedte van het verouderde stuk gebruikt de klok van het apparaat, binnen grenzen van de server.** Het
   antwoord van `GET /zones/{id}/history` bevat geen servertijd. Het stuk loopt daarom tot "nu" volgens het apparaat,
   maar nooit korter dan de verouderd-termijn na de laatste meting (anders had de server de sensor niet verouderd
   genoemd) en nooit verder dan 24 uur na de laatste meting. Een scheve klok kan het stuk dus hoogstens iets te smal of
   te breed tekenen; **of** de sensor verouderd is en **sinds wanneer** komen altijd van de server.
3. **De doellijn loopt het verouderde stuk niet in.** De traplijn van het doel liep tot het eind van het uur van zijn
   laatste waarde (ADR-0013 punt 2) en stak daardoor tot een uur de arcering in, alsof er daar nog iets bekend was. Bij
   een verouderde sensor stopt de doellijn nu bij de grens van het verouderde stuk. Komt het doel nog wél binnen terwijl
   de temperatuursensor zwijgt, dan loopt de doellijn door tot haar eigen laatste waarde, over de arcering heen: de
   arcering betekent hier "geen gemeten temperatuur", en het doel is echt bekend. Peil §10 zegt niets over een
   grenslijn in een verouderd stuk; dit is de voorzichtigste lezing (niets tekenen wat niet gemeten is).
4. **`<figure>` met `figcaption`; de tabel staat erbuiten.** Het plotvak, de tijdas, de leesregel en het bijschrift
   vormen één `<figure class="hist-figuur">`. Het bijschrift (`figcaption`) bevat, in deze volgorde: de verouderd-regel
   (alleen als verouderd), "Gearceerd: geen meting (nooit 0)." met een staaltje (alleen als er iets gearceerd is) en de
   bronregel: "Bron: sensor van de airco · bijgewerkt 11 nov 08:29 · een gat betekent dat er geen meting was (nooit
   0)." Het laatste deel vervalt zodra de arceer-uitleg er al staat (niet twee keer hetzelfde). De bron is "sensor van
   de zone", "sensor van de airco" of, als de server geen bron noemt, "temperatuursensor". `<details>` "Toon als tabel,
   per uur" staat **na** de figuur, niet erin: het Peil-prototype zet `details` na `figcaption` binnen `figure`, maar
   HTML staat `figcaption` alleen als eerste of laatste kind toe. De tabel is ook geen bijschrift maar een tweede
   weergave van dezelfde gegevens.
5. **Eindpunt van 12 px, waarde ernaast alleen waar er plaats is.** De laatste meting met waarde krijgt een stip van
   12 px in de reekskleur met een ring van 2 px in de kaartkleur (COMPONENTEN.md), op elke breedte en ook bij een
   verouderde sensor (daar is het "de laatst bekende waarde"). Het **directe label** ("20,8°") staat alleen bij een
   verse sensor en vanaf een grafiek van 34 rem breed, in een eigen strook rechts van het tekenvlak. Het prototype zet
   het label over de lijn heen; op 320–390 px bedekt dat het laatste stuk van de lijn. Smaller dan 34 rem staat de
   waarde in het bedieningsblok boven de grafiek (daar staat de gemeten temperatuur groot) en in de toegankelijke naam.
   Bij een verouderde sensor geen direct label: een los getal zonder "verouderd" erbij leest als een actuele waarde.
   **De strook heeft geen vaste breedte maar volgt het label** (aangepast op 11 november 2026, na de eerste
   oplevering). Een onzichtbare kopie van het label (`hist-eindmaat`: zelfde tekst, letter, marge en vulling;
   `aria-hidden`, `visibility: hidden`) staat als laatste kind in het plotvak en in de rij van de tijdas; het
   tekenvlak en de tijdas nemen de rest (`flex: 1 1 0`). Zo is de strook in elk lettertype en bij elke waarde precies
   breed genoeg, eindigt het label 8 px binnen het plotvak en blijft de tijdas even breed als het tekenvlak. De
   eerste versie had een vaste strook van 56 px. Die paste bij "20,0°" in Noto Sans (42 px), maar niet in DejaVu Sans
   (49,5 px; het systeemlettertype van de CI-machine), en ook in Noto Sans niet bij "100,0°" of "-10,5°". De web-app
   gebruikt het lettertype van het apparaat (`system-ui`); een maat die van één lettertype uitgaat is daarom een
   aanname en geen meting. Afgewezen: een bredere vaste strook (past weer bij één lettertype en kost op elke breedte
   tekenruimte), en het label afkappen (een afgekapt getal is erger dan geen getal).
6. **Tijdas met hele uren vanaf 34 rem.** Begin- en eindtijd staan altijd links en rechts; daartussen komen hele
   klokuren op een vaste stap (1, 2, 3, 6 of 12 uur, hoogstens acht stappen), in de tijdzone van het huis. Uren binnen
   15 % van het begin of het eind vervallen, zodat labels elkaar niet raken. Smaller dan 34 rem alleen begin en eind
   (COMPONENTEN.md: "tussenlabels pas vanaf `sm`").
7. **Alles binnen het plotvak.** Het plotvak heeft 6 px ruimte boven en onder; de y-labels vallen er helemaal binnen
   en de focusring raakt geen tekst meer. De tijdas staat 6 px onder het plotvak (de ring is 3 px op 2 px afstand).
8. **Legenda alleen bij een grafiek.** Zonder gegevens ("Nog geen geschiedenis", "Geen meting in de laatste 24 uur")
   is er geen legenda. De staaltjes zijn 20 px breed, zodat het streepjespatroon van "Doel" (6/4) als streep te lezen
   is en niet als één blokje.
9. **Zonder Tailwind-preflight.** my_house laadt de preflight bewust niet; de standaardrand van een element is daar
   `medium`. `border-dashed` alleen tekent dan een kader aan vier kanten. De rand van het verouderde stuk is daarom
   `border-0 border-l-2 border-dashed`. Dit is een valkuil voor elke nieuwe Peil-klasse met een rand in deze repo.

## Bewust niet veranderd

- **De arcering zelf** (`--chart-gap`): 1,55:1 (licht) en 1,58:1 (donker) tegen de kaart, dus onder 3:1. Het is een
  Peil-token; de kopie in deze repo is gepind. De arcering is hier nooit het enige teken: een gat heeft het woord
  "Geen meting" (als het past) en de uitleg onder de grafiek, een verouderd stuk heeft de rand van 6,36/7,78:1, de pil
  en de tekst. Voorstel aan Peil: het token ophogen of een rand om elk gearceerd vlak voorschrijven.
- **Teksten van ADR-0013** (uitleg van de toetsen, "Toon als tabel, per uur", de leesregel), de tabel en de
  toetsenbediening.
- **Offline** (ADR-0011) en de weergave van een sensor die maar zelden meldt (ADR-0013).
- **De vrijgehouden plaats voor de leesregel** bij een muis (ADR-0013 punt 4). Op een smal venster op een computer is
  dat een lege strook onder de tijdas; op een echt aanraakscherm bestaat die strook niet.

## Gevolgen

- Code: `apps/web/src/climate/history.ts` (`staleSince`, `historyRange`/`historySlots` met een eindtijd, `lastMeasured`,
  `gapsBefore`, `hourTicks`, `momentLabel`, `hasValues`), `apps/web/src/climate/HistoryChart.tsx` (figuur, bijschrift,
  eindpunt, verouderd stuk, tijdas), `apps/web/src/pages/Climate.tsx` (geeft het oordeel van de server en de bron door;
  legenda alleen bij gegevens) en de haakklassen `hist-*` in `apps/web/src/peil/componenten.css`. Geen nieuwe API, geen
  nieuw datamodel, geen nieuwe route of instelling; de Android-app heeft deze grafiek niet.
- Tests: `src/test/history-chart.test.tsx` (vitest; bij deze review 38 tests, was 19) en `e2e/12-grafiek-24-uur.e2e.ts` (bij deze
  review 5, was 4 — eerder stond hier 6 en 5, dat telde de onboarding-test mee die ervoor draait). Na de technische review
  (11 november 2026): 42 en 7 (vegen en slepen, knijpen, de legenda, de tijden in het bijschrift), en na de herreview
  43 en 10 (knijpen kiest geen uur, ook als de tweede vinger later komt; `PEIL_REVIEW.md` §12.9, aanvulling). De nieuwe browsertest laat een airco twee uur zwijgen tot de **server** de sensor verouderd noemt en controleert
  in licht en donker op 390 en 1440 px: lijn en doellijn stoppen bij de grens, gearceerd stuk met stippelrand, de pil,
  "Geen meting" in leesregel en tabel, het label "Verouderd sinds …" binnen het stuk op één regel (1440) of afwezig
  (390), en axe-core 0.
- Bewijs: `docs/platform-v2/EVIDENCE.json`, regel `REVIEW-my_house-grafiek-24-uur` (status blijft Bezig);
  `docs/ontwerp/PEIL_REVIEW.md` §12 met de metingen; afdrukken
  `docs/screenshots/peil/review/12-grafiek-24-uur-*.png` (vier nieuw: `…-verouderd-…`).
- Open bij de bron (ai-server-manager, niet in deze repo te wijzigen):
  1. het prototype `server-metrieken.html` zet `<details>` na `<figcaption>` binnen `<figure>` (ongeldige volgorde);
  2. het prototype zet het eindlabel en het verouderd-label over de gegevens heen; een regel "alleen als het past,
     anders in het bijschrift" ontbreekt;
  3. `--chart-gap` haalt als vlak geen 3:1;
  4. §10 zegt niet wat een doel- of grenslijn doet in een verouderd stuk;
  5. het history-antwoord heeft geen servertijd (een veld `as_of` zou de klok van het apparaat overbodig maken; dat is
     een API-wijziging en valt buiten een ontwerpreview).
- Bekend en niet in deze stap: een echte schermlezer (NVDA, JAWS, TalkBack, VoiceOver), echte telefoons en tablets,
  Firefox en Safari en gegevens van een echte airco zijn niet getest — **NIET GEVALIDEERD (hardware)**. Alles is
  gemeten in Chromium met testgegevens (Sim-getest). De volledige afdrukken `klimaat-bediening-*` in
  `docs/screenshots/peil/na/` tonen nog de grafiek van vóór ADR-0013.
