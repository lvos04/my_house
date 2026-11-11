# my_house — onafhankelijke ontwerpreview tegen Peil 1.1.0 (stap 5)

**Datum:** 9 november 2026 · **Branch:** `ccr-c133d7a0-kuz1cv` · **Rol:** ontwerp-agent, onafhankelijk van de bouwers
van stap 3 (web) en stap 4 (Android) · **Maatstaf:** Peil 1.1.0 (`design/ontwerptaal/`, ONTWERPTAAL.md en
COMPONENTEN.md), de prototypes en afdrukken van Peil (`definitief/prototypes`, `definitief/shots`), de besluiten van de
gebruiker (BESLUITEN_GEBRUIKER.md) en WCAG 2.2 AA · **Besluit bij deze review:**
[ADR-0011](../adr/ADR-0011-peil-review-verouderd-en-nooit-opgehaald.md).

Vaste regels van deze ronde: alleen uiterlijk, teksten, toegankelijkheid en de twee toegestane toevoegingen. Routes,
API-aanroepen, foutformaat, datamodel, poorten, installatie, `data-testid`'s en rollen zijn niet veranderd; de
OpenAPI-export is byte-gelijk gecontroleerd.

---

## 1. Oordeel in het kort

De omzetting is goed en dicht bij Peil: kleuren, typografie, randen, statusvormen, de ondernavigatie en de Meer-sheet
komen overeen met de prototypes, en de meetbare basis (axe, tekstcontrast, doelen, overloop) was al op orde. De review
vond toch **elf echte fouten** die de geautomatiseerde tests van stap 3 niet zagen, omdat ze iets anders meten (de
pagina als geheel, niet de inhoud van één knop; een stilstaande pagina, niet de offline-toestand; axe, niet de
Tab-doorloop). Alle elf zijn gerepareerd (§4); twee daarvan (10 en 11) vond pas de tweede ronde en de nieuwe
Tab-doorloop in de browsertests. Wat open blijft, staat in §6 en §7; niets daarvan is als geslaagd
gemeld.

| | Ronde 1 (vóór de reparaties, build `ff9744f`) | Ronde 2 + 3 (na de reparaties) |
|---|---|---|
| Metingen (scherm × thema × breedte) | 320 (27 schermen/toestanden) | 332 (28, met "nooit geladen, na de herhaalpogingen") + 96 in ronde 3 (korte en offline pagina's na reparatie 11) |
| axe-core 4.14, WCAG 2.0/2.1/2.2 A+AA | 0 | 0 |
| Tekst < 4,5:1 (in de gerenderde pagina) | 0 (laagste 4,56:1, de bewust gedempte laatst bekende waarde op de arcering) | 0 (laagste 4,56:1, idem) |
| Veld- en knopgrenzen < 3:1 | 0 velden; 2 knoppen (hovertoestand, zie §5.2) | 0 velden; 0 knoppen buiten dezelfde hovertoestand (laagste 3,07:1) |
| Stippelranden < 3:1 | 48 (decoratieve stippellijnen, offline apparaatkaart) | 0 |
| Doelen < 44 px · tekst < 14 px · zijwaartse scroll | 0 · 0 · 0 px | 0 · 0 · 0 px |
| Inhoud buiten zijn knop/tegel | 3 plekken (modusknop 8–13 px, lichttegel 18 px, ondernavigatie 2 px) | 1: het vette "Overzicht" in de ondernavigatie op 320 px, 2 px (open punt, §6) |
| Focus helemaal bedekt (Tab-doorloop) | 2× (weekschema 390 px) | 0; de e2e-doorloop vond daarna nog ≥ 1024 px met een leeg schema (bevinding 10), ook gerepareerd |
| Koppen: geen h1 · sprongen · kapotte aria-verwijzingen | 12 · 4 · 15 | 0 · 0 · 0 |
| Groen bij Onbekend/Verouderd | 1 (offline: "Achtergronddienst — Gezond") | 0 |
| "Laden…" op een nooit opgehaalde pagina zonder verbinding | ja (geen einde) | nee: "Onbekend — nog niet opgehaald" met reden, ook na de herhaalpogingen |

---

## 2. Werkwijze

- **Echte app met fictieve testgegevens.** Wegwerp-database in de gedeelde PostgreSQL 16, de echte API
  (`shc-api serve`), de gebouwde web-app achter `vite preview` (zelfde opzet als `apps/web/e2e/run-stack.sh`), de
  testgegevens van stap 2 (`seed_peil.py`: twee Gree-units, Zigbee-sensoren, Hue-groepen aan/uit/onbereikbaar,
  P1/kWh-meters, boilerstekker, tarief, meldingen, weekschema, beheerder en kijker). Toestanden: lege database,
  uitgelogd, geen woning, kijker, sensorwaarden 3 uur oud, API gestopt (offline, ook een nooit geladen pagina, direct en
  na de herhaalpogingen) en een nieuwe sessie zonder server. Het Hub-adres is fictief (`https://hub.voorbeeld.invalid:8443`).
- **Meetscript** `docs/screenshots/peil/werkwijze/review.mjs` (Playwright 1.65, Chromium, nl-NL, Europe/Amsterdam,
  `prefers-reduced-motion`), per scherm × licht/donker × **320, 360, 390, 768, 1024, 1440 px**: axe-core 4.14 met de
  tags `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa`; contrast van elke tekstknoop **in de gerenderde pagina** (met de
  methode van Peil `bouw/controle.cjs`: lagen, opacity, color-mix en de donkerste kleur van een arcering); de grens van
  elk veld, elke knop, tab, tegel en uitklapkop t.o.v. zijn omgeving (rand, eigen vlak of inset-balk); stippelranden;
  doelgrootte; overloop; inhoud die buiten zijn eigen element steekt; koppen, dubbele id's en aria-verwijzingen; een
  groen-audit (elke kleur, rand, vulling en `::before` in een gezondheidsgroen of het aqua van grafiekslot 3) en
  "alleen kleur"-kandidaten (kleine elementen met een statuskleur zonder tekst of naam). Op 390 en 1440 px daarnaast
  een **volledige Tab-doorloop** (106 doorlopen, ± 2 150 stops): ring aanwezig, ringcontrast, helemaal of deels bedekt
  door een vaste balk. Samenvatting met `analyse.py`.
- **Afdrukken** (volledige pagina) op 360/390/768/1440 × licht/donker van alle hoofdschermen en toestanden, zelf
  bekeken en naast de Peil-prototypes gelegd (§5.1).
- **Gerichte nametingen** voor elke bevinding op een aparte stack, daarna een volledige tweede ronde na de reparaties.
- **Android:** de 80 afdrukken van stap 4 (Paparazzi) in licht en donker bekeken; de bron gecontroleerd op dezelfde
  punten; tests en `verifyPaparazziDebug` opnieuw gedraaid (§5.5).
- Skills: design:accessibility-review, design:design-critique, design:ux-copy, design:design-system,
  design:design-handoff en dataviz (vóór de grafiekwijziging).

---

## 3. Vergelijking met de Peil-prototypes (design-critique)

**Eerste indruk.** Rustig en eerlijk: bovenaan staat wat aandacht vraagt ("Wat actie nodig heeft" met Beperkt),
daaronder de systeemstatus en de zones. Dat volgt het patroon "Overzicht/Home" (Peil §9) en het prototype `home.html`.
Het oog valt eerst op de begroeting en het aandachtsblok — dat is goed.

| Onderdeel | Prototype (`huis.html`, `home.html`, `offline.html`) | my_house na de review | Oordeel |
|---|---|---|---|
| Ondergrond, kaarten, randen | koel neutraal, kaart met 1 px rand, geen schaduw | gelijk, licht en donker | ✓ |
| Typografie | systeemfont, h1 24, kaartwaarde 30, meta 14 | gelijk; nergens < 14 px | ✓ |
| Status | pil met icoon + woord; Gezond zonder vlak; Onbekend gestippeld | gelijk (StatusBadge) | ✓ |
| Lichttegel | aan = `domain-light-on-bg` + rand, uit = kaart, onbereikbaar = stippel + Onbekend | gelijk; op 320 px nu één kolom | ✓ (na reparatie) |
| Modusknoppen | gekozen = domeinvlak + vinkje | gelijk; het vinkje paste niet (gerepareerd) | ✓ (na reparatie) |
| Draaiknop/doel | ring in `domain-heat-soft`, −/+ 44 px | gelijk | ✓ |
| Verouderd | gearceerd vak, stippelrand, "Laatst bekend · 06:40" | arcering + "Verouderd"-pil + stale-ink; kaartrand nu gestippeld | ✓ (na reparatie) |
| Offline | banner `unknown`, elke kaart verouderd, nooit groen | banner gelijk; groen "Gezond" en vaste kaartrand gerepareerd | ✓ (na reparatie) |
| Readback | "Verstuurd — wacht op bevestiging van my_house" | "Verzonden, nog niet bevestigd" | bewust anders, zie §8 punt 1 |
| Grafiek trend | lijn met as-labels en tabel | as-labels toegevoegd; tabel niet (vraagt akkoord) | deels, zie §6 |
| Schil | zijbalk ≥ 1024, ondernavigatie < 1024, Meer-sheet | gelijk; "Terug naar de Hub" en de themaknop op de afgesproken plekken | ✓ |

**Hiërarchie en consistentie.** Eén h1 per pagina (nu ook tijdens laden en bij een fout), secties als h2, kaarttitels
op 18 px. Dezelfde woorden voor dezelfde begrippen in web en Android (Gezond · Beperkt · Storing · Onbekend ·
Verouderd, Info · Middel · Hoog, "Wat actie nodig heeft", "geen meting"). Wat goed werkt: de rust van de neutrale
ondergrond, de duidelijke grens van elke knop (≥ 3:1, laagste 3,07:1), het eerlijke "Onbekend — niet bereikbaar" bij
de tuinlampen, de boiler als "Alleen meten" zonder schakelaar.

---

## 4. Bevindingen en reparaties

| # | Bevinding (gemeten in de echte pagina) | Norm | Ernst | Reparatie | Commit |
|---|---|---|---|---|---|
| 1 | Zonder verbinding toonde het overzicht "Achtergronddienst — **Gezond**" (groen) over een stand die niet meer gemeten wordt; Android deed hetzelfde op Start | Peil §1, §4.4 ("verouderd is nooit gezond") | hoog | laatst bekende Gezond → **Verouderd** (web: `useStatusNu` in StatusBadge; Android: `PeilUi.systeem(…, verouderd)`); Beperkt/Storing blijven (ADR-0011) | `4678a8d`, `b24f021` |
| 2 | Offline hadden de kaarten een vaste rand: het kaartrecept van fase 2 overschreef de stippelregel van de aliaslaag | Peil §9 offline | middel | `.from-snapshot`/`.is-offline` kaarten: 2 px stippel in `--stale`; aandachtsblok houdt zijn statuskleur | `4678a8d` |
| 3 | Een nooit opgehaalde pagina bleef zonder verbinding "Laden…" tonen, zonder h1 | Peil §9, §11.1; A-02 | middel | "**Onbekend** — nog niet opgehaald" met reden (`note unknown`), h1 tijdens laden en fout (`Loading titel`, `PaginaFout`) | `4678a8d` |
| 4 | "✓ Verwarmen" stak 8–13 px buiten de modusknop (390–1440 px) | Peil §6/§12 (reflow, niets afgekapt of overlappend) | middel | kolommen ≥ 8,5 rem, icoon mag boven het woord | `6b4ef3b` |
| 5 | Lichttegel "Woonkamer" stak op 320 px 18 px buiten de tegel | Peil §6 reflow 320 | middel | hooguit twee tegels, één kolom als een tegel te smal wordt; lange naam breekt binnen de tegel | `6b4ef3b` |
| 6 | Weekschema < 768 px: focus op de dagvinkjes van "Dag kopiëren" lag **helemaal** achter de vaste opslaanbalk (113–142 px hoog, scroll-padding rekende 72 px) | WCAG 2.4.11; Peil §6 scroll-padding | middel | `--actiebalk-h: 9rem` onder 768 px | `6b4ef3b` |
| 7 | Offline apparaatkaart (in het ingeklapte blok "Lampen, groepen en scènes", zichtbaar na uitklappen): stippelrand in de decoratieve `--border` (2,0:1 licht, 1,8:1 donker); decoratieve scheidingslijnen gestippeld (kosten, knoppenkoppelingen, beheerpaneel) | WCAG 1.4.11; Peil §4.2 (stippel = onbekend/verouderd) | laag | apparaat offline: `--unknown` (≥ 3:1); scheidingslijnen doorgetrokken | `4678a8d` |
| 8 | Kopsprongen h1→h3 (Account, Verlichting), h2→h4 (Weekschema); 15× `aria-controls` naar een gesloten (niet bestaand) paneel (Apparaten) | Peil §12 matrix; WCAG 1.3.1, 4.1.2 | laag | juiste kopniveaus met de oude stijl (geen zichtbare verandering); `aria-controls` alleen als het paneel open is | `5857172` |
| 9 | De lijngrafiek "Laatste 24 uur" had geen schaal (geen graden, geen tijden) | Peil §5 ("elk getal heeft eenheid … tijd"), §10 | laag | rasterlijnen op ronde graden met het getal, begin- en eindtijd eronder, in `chart-label` (dataviz-regels) | `757bf79` |
| 10 | Weekschema ≥ 1024 px met een **leeg** schema: focus op een veld van een nieuw blok helemaal achter de vaste opslaanbalk (61 px; scroll-padding onder 0,75 rem) — gevonden door de nieuwe Tab-doorloop in `e2e/11-peil` | WCAG 2.4.11 | middel | vanaf 1024 px scroll-padding = actiebalk + 0,75 rem | `1f6b124` |
| 11 | Korte pagina's < 1024 px (offline, nog niets opgehaald): ± 136 px leeg vlak tussen kopbalk en inhoud (`.shell` flex-wrap verdeelde de vrije hoogte over de regels; bestond al vóór Peil) | Peil §6 (opbouw), §9 offline | laag | `.shell { align-content: flex-start }` onder 1024 px | `1f6b124` |
| — | Peil §11.1 "niet meer: Gelukt": back-upstatus "Gelukt" | woordenlijst | laag | "Gemaakt" | `5857172` |

De nieuwe controles zitten nu in de tests, zodat deze fouten niet terugkomen: `e2e/11-peil` (inhoud binnen het
element in de matrix 320–1440 px; volledige Tab-doorloop op 390/1440 in licht en donker met ring ≥ 3:1 en "niet achter
een vaste balk"), `e2e/08-offline` (geen `data-status="healthy"` offline, gestippelde kaart, nooit opgehaalde pagina
met h1 en Onbekend) en `src/test/peil-review.test.tsx`; Android `PeilUiTest`.

**Negatieve controle (eerlijk):** met de regels voor modusknop, lichttegel en opslaanbalk tijdelijk teruggedraaid faalde
de nieuwe Tab-doorloop op 390 px (4× "ligt achter een vaste balk") — en ook op 1440 px (8×), wat bevinding 10
blootlegde; met de reparaties slaagt hij (13/13 in `11-peil`, 25/25 in de volledige run). De inhoudcontrole van de matrix
bleef in die run groen: in de e2e-testgegevens staat geen zone op Verwarmen en heeft geen lichtgroep een lange naam,
dus die controle is in de browsertests **niet negatief getoetst**; in `review.mjs` vond dezelfde meting wel de drie
plekken van ronde 1 en daarna alleen nog de ondernavigatie.

### 4.1 Handoff van de reparaties (voor de Hub, MediaGuard en MSO)

| Onderdeel | Specificatie |
|---|---|
| Keuzeknoppen met icoon (modus) | rooster `repeat(auto-fit, minmax(min(8.5rem, 100%), 1fr))`, gap 8 px; knop `min-h-12`, `flex-wrap`, `gap-x-1.5 gap-y-0.5`, `text-center`; gekozen = domein-`-bg` + `-ink` + 2 px rand + vinkje (`check`) vóór of boven het woord; nooit breder dan de knop |
| Tegels in twee kolommen | `minmax(max(8.5rem, calc(50% - gap/2)), 1fr)`: hooguit twee, één kolom als een tegel < 8,5 rem wordt; naam `overflow-wrap: anywhere` als laatste redmiddel |
| Vaste actiebalk | scroll-padding onder = ondernavigatie + werkelijke balkhoogte + 12 px op elke breedte, ook ≥ 1024 px als de balk daar vast blijft (Peil zet hem ≥ lg in de flow) |
| Offline/verouderd | StatusBadge `healthy` + offline → `stale` (`clock-alert`, stippel + arcering); `degraded`/`down` blijven; kaarten 2 px stippel in `--stale`; aandachtsblok stippel in zijn statuskleur |
| Nooit opgehaald | `note unknown` (`circle-help`, stippel `--unknown`): "**Onbekend** — nog niet opgehaald." + reden + wat er gebeurt; geen `aria-busy`; paginakop h1 blijft staan |
| Lijngrafiek-as | ticks op ronde waarden (stap 1, of 2 bij > 6 eenheden) links in `chart-label` 14 px tabelcijfers; begin- en eindtijd onder het plotvlak; tekst `aria-hidden`, de samenvatting blijft in de naam van de grafiek |

---

## 5. Controle per eis

### 5.1 Afdrukken en vergelijking

- Ronde 1: 215 afdrukken (27 schermen/toestanden × 360/390/768/1440 × licht/donker, de Meer-sheet alleen < 1024 px,
  plus forced colors), ronde 2: 223, ronde 3: 65. Bekeken als contactvellen naast de Peil-afdrukken
  (`definitief/shots/huis-*`, `home-*`, `offline-*`) en ingezoomd op elke meldingsplek. De meetscripts vonden de
  meeste fouten; drie vond pas het kijken: het vinkje naast "Verwarmen" (daarna als meting toegevoegd), het lege vlak
  boven korte pagina's en de ontbrekende schaal van "Laatste 24 uur".
- In de repo: de schermen die door de review veranderden zijn in `docs/screenshots/peil/na/web/` vervangen (zelfde
  namen; `index.json` veld `bijgewerkt`), plus de nieuwe toestand `verlichting-offline-nooit-geladen-later-*`; uitsneden
  voor/na per bevinding in `docs/screenshots/peil/review/` (1 offline groen, 3 nooit opgehaald, 4 modusknop, 5
  lichttegel 320 px, 7 offline apparaat, 9 grafiekschaal). Afdrukken die niet veranderden (bijv. Apparaten: de offline
  apparaatkaarten staan in het ingeklapte blok "Lampen, groepen en scènes") zijn niet opnieuw vastgelegd.
- Echte LAN-adressen van de testmachine zijn afgedekt (zelfde regel als stap 2); het Hub-adres is fictief.

### 5.2 axe, contrast, overloop, doelen, toetsenbord en focus

- **axe-core 4.14** (WCAG 2.0/2.1/2.2 A+AA): 0 overtredingen in alle drie rondes, op alle schermen, breedtes en thema's.
- **Tekstcontrast in de pagina ≥ 4,5:1:** 0 fouten. Laagste waarde 4,56:1: de laatst bekende waarde (`--stale-ink`) op
  de donkerste arceerpixel — de bewuste Peil-uitzondering (Peil §3.3, ≥ 4,5:1). Tussen 4,5 en 7:1 (onder het Peil-niveau
  voor gewone tekst, maar AA): alleen de stale-ink-teksten (≈ 6:1, bedoeld) en de labels in de schemaband
  ("Dag 21,0°" `domain-heat-ink` op `domain-heat-soft`: 5,49:1 licht, 4,93:1 donker; "Nacht 18,0°" 6,22:1). Dat paar
  staat in de brede Peil-matrix (laagste 4,93), maar Peil noemt `-soft` decoratief; zie §8 punt 3.
- **UI-grenzen ≥ 3:1:** alle invoervelden ≥ 3:1; elke knop met een rand of vlak ≥ 3:1 (laagste 3,07:1). De meldingen
  (2 in ronde 1, 4 in ronde 2 en 3) waren allemaal de **hovertoestand** van "Terug naar de Hub" (accentvlak 1,29:1 t.o.v. de zijbalk): een
  stille knop wordt door zijn tekst herkend, het hovervlak is extra terugkoppeling — geen fout. Tekstknoppen zonder
  grens (navigatie, onderstreepte tabs ≥ 768 px, stille knoppen) worden door hun tekst herkend; de gekozen stand heeft
  een balk in `--primary` (≥ 3:1).
- **Focus:** elke stop heeft de tweekleurige ring (3 px `--ring` + 2 px halo), laagste ringcontrast 7,68:1 (licht) en
  7,55:1 (donker). Uitzondering zonder eigen ring: het klokknopje in tijd- en datumvelden (Weekschema, Energie, Optimalisatie) — dat tekent de browser
  zelf met een zwarte of witte ring van 2 px (bekeken in licht en donker, goed zichtbaar); Peil kan daar niet bij.
  Geen focus meer helemaal bedekt (was 2× in het weekschema). "Deels bedekt" bij de skiplink is een meetpunt in de
  afgeronde hoek; bij het tabpaneel van Account (groot element) blijft het grootste deel zichtbaar (2.4.11 AA vraagt
  "niet helemaal").
- **Toetsenbord:** skiplink "Naar de inhoud" is de eerste stop op elke vers geladen pagina met schil (na een routewissel
  of in de Meer-sheet begint de focus bewust in de inhoud of het menu); volgorde volgt de leesvolgorde;
  geen focusval (de Meer-sheet houdt de focus vast zolang hij open is, Esc sluit). De volgorde is gemeten, niet met een
  schermlezer beluisterd (§7).
- **Overloop op 320 px:** 0 px op alle schermen en toestanden.
- **Doelen ≥ 44 px:** 0 kleinere doelen (inline links in lopende tekst uitgezonderd, WCAG 2.5.8; Android ≥ 48 dp in de
  code, zie §5.5).
- **Forced colors:** stippelranden blijven gestippeld (`note unknown`, `pill unknown`, onbereikbare lamp).

### 5.3 Onbekend/verouderd nooit groen, status nooit alleen kleur, microcopy

- **Groen-audit:** gezondheidsgroen komt alleen voor bij StatusBadge "Gezond" (Achtergronddienst, Woning, Netwerkdetectie)
  met een verse meting. Het aqua van grafiekslot 3 komt alleen voor bij de boiler in grafiek en legenda (Peil: domein =
  grafiekslot). Na reparatie 1: **geen groen meer bij offline, onbekend of verouderd** (ronde 2 en 3: 0 treffers; het
  offline overzicht toont "Achtergronddienst — Verouderd").
- **Status nooit alleen kleur:** elke status heeft icoon + woord + vorm; de enige kandidaten van de automatische zoektocht
  waren het vinkje-icoon náást het woord "Gezond" (geen fout). Grafieken: legenda + tekst, onvolledig gestippeld en
  gearceerd, energie met "Toon als tabel".
- **Woordenlijst (Peil §11.1):** Gezond · Beperkt · Storing · Onbekend (+ reden) · Verouderd · Gepauzeerd ·
  Info/Middel/Hoog · "Wat actie nodig heeft" · "geen meting" staan in web en Android gelijk. Gevonden en gerepareerd:
  "Laden…" als eindtoestand (nu Onbekend + reden) en "Gelukt" (nu "Gemaakt"). Bewust gelaten (domeintekst, Peil §13):
  "Let op: de HomeWizard-app kan de stekker nog wel schakelen" (Apparaten), de wizardstap "Klaar" en "Klaar om te
  installeren." (neutraal, geen groen), "Verzonden" (§8 punt 1). Het "!" in de energielegenda is het teken voor
  "Opmerking", geen uitroep.

### 5.4 Toegestane toevoegingen

- **Themaknop Systeem/Licht/Donker (O-09):** in de zijbalk en de Meer-sheet; `aria-pressed`, groep "Thema", elk
  ≥ 44 px; Donker blijft na herladen, Systeem volgt het apparaat (e2e `11-peil`, geslaagd); afdrukken
  `thema-donker-gekozen-*` bekeken. Vóór het inloggen geen knop, wel het juiste thema.
- **"Terug naar de Hub" (O-11):** alleen met een geldig adres uit `/_shc/config.json`, in zijbalk en Meer-sheet, zelfde
  tabblad, ≥ 44 px; verborgen bij leeg, `javascript:`, pad of loopback (e2e `11-peil`, geslaagd). In de Tab-doorloop
  is het de tweede stop (na de skiplink) op ≥ 1024 px. De nginx-kant (HUB_URL-validatie, `no-store`) is in stap 3
  getest; deze review heeft de webcontainer niet opnieuw gebouwd.

### 5.5 Android (afdrukken licht en donker)

- **Bekeken:** alle 80 afdrukken van stap 4 (`docs/screenshots/peil/android/`, Paparazzi, 360/393 dp × licht/donker):
  Start (ook offline, kijker, storing), Klimaat (ook conflict en offline), Energie, Meer, Koppelen, Niet meer gekoppeld en
  de onderdelenpagina. Licht en donker gebruiken dezelfde Peil-tokens als de web-app; status is overal icoon + woord +
  vorm; offline is gestippeld met "Verouderd · Laatst bekend", zonder dimmen; de boiler gebruikt het aqua van
  grafiekslot 3 alleen in grafiek en legenda; ernst is Info/Middel met de linkerbalk; doelen zijn ≥ 48 dp in de code.
- **Gevonden:** dezelfde fout als op het web — de statusregel op Start bleef offline "Gezond" (groen) als er geen
  probleem bekend was. Gerepareerd in `PeilUi.systeem(level, verouderd)` en `HomeScreen` (`b24f021`), getest in
  `PeilUiTest`. De offline-afdruk toont Beperkt (geen back-up) en verandert dus niet; `:app:verifyPaparazziDebug`
  slaagt met de bestaande 56 referentiebeelden.
- **Gezien en gelaten:** de waarde in een verouderde zonekaart staat in stale-ink met een stippelrand maar zonder
  arceervak (ADR-0010, Compose); de draaiknop houdt offline zijn domeinkleur (decoratief, de waarde zelf is gedempt);
  het zoekkader over het camerabeeld blijft wit. Geen van drieën is een fout tegen Peil.
- **Niet gedaan:** geen toestel of emulator (geen KVM), geen TalkBack, widget niet in een afdruk — **NIET GEVALIDEERD
  (hardware)**.

---

## 6. Niet gerepareerd of bewust gelaten

| Punt | Waarom niet | Voorstel |
|---|---|---|
| Ondernavigatie op 320 px: het vette "Overzicht" (actief) is 2 px breder dan zijn kolom van 64 px (Noto Sans; met bredere lettertypen meer). Niet afgekapt en leesbaar | vijf kolommen van 64 px zijn te smal; 14 px is de ondergrens, vet is de Peil-aanduiding voor actief | Peil 1.1.1: regel voor 320 px (bijv. actief zonder vet, alleen de balk) — besluit bij de bron; de e2e-controle sluit de ondernavigatie bewust uit |
| Tabelweergave bij "Laatste 24 uur" (WCAG-tweeling, Peil §10) | functionele toevoeging; het besluit van de gebruiker gold alleen MediaGuard/MSO | ~~akkoord vragen (open sinds stap 2)~~ **gedaan na deze review:** akkoord op 10 november 2026, tabel per uur en toetsenbordbediening gebouwd op 11 november 2026 (`PEIL_MIGRATIE.md` §18, ADR-0013); onafhankelijk nagekeken in §12 (ADR-0014) |
| Labels in de schemaband op `domain-*-soft` (4,93–6,22:1) | AA gehaald; Peil zet het paar zelf in de brede matrix | Peil 1.1.1: label op het `-bg`-vlak of een donkerdere `-soft` (§8 punt 3) |
| Klokknopje in tijd-/datumvelden zonder Peil-ring | browseronderdeel; de browserring is zichtbaar | geen |
| Readback "Verzonden" i.p.v. Peil "Verstuurd" | my_house is eigenaar van de readback; tests en Android gebruiken "Verzonden" | Peil 1.1.1 neemt "Verzonden" over (§8 punt 1) |

---

## 7. Niet getest (NIET GEVALIDEERD)

- **Echte schermlezer** (NVDA, TalkBack, VoiceOver): niet beschikbaar. De toegankelijke namen, rollen en koppen zijn
  automatisch gecontroleerd, niet beluisterd.
- **Echte apparaten:** telefoon, tablet, wandpaneel, fel zonlicht: **NIET GEVALIDEERD (hardware)**. Welke browsers de
  apparaten van de gebruiker hebben is onbekend (Tailwind 4 vraagt Safari 16.4+, Chrome 111+, Firefox 128+).
- **Android op een toestel of emulator:** geen KVM-toegang voor deze gebruiker; alleen Paparazzi (Sim-getest).
  Aanraakdoelen ≥ 48 dp staan als maten in de code en zijn niet in een gerenderd scherm gemeten. De widget (Glance) is
  niet vastgelegd.
- **200 %-zoom** is alleen benaderd via de breedtes 640/768 px van de matrix, niet met echte browserzoom.

---

## 8. Voorstellen voor Peil 1.1.1 (bron: ai-server-manager)

1. Readback-woord: "Verzonden — wacht op bevestiging" (my_house) in plaats van "Verstuurd" (sinds stap 2 open).
2. Ondernavigatie op 320 px: actief zonder extra gewicht, of een kortere vaste set labels.
3. `domain-*-soft` draagt in my_house tekst (schemaband): als tekstvlak een eigen paar met ≥ 7:1, of tekst op `-bg`.
4. Rand-token voor gekozen domeinknoppen (al voorgesteld in stap 4, ADR-0010 punt 5).
5. Een regel voor offline/verouderd op statuspillen: "laatst bekende Gezond = Verouderd; Beperkt/Storing blijven" (ADR-0011), zodat de Hub en MediaGuard hetzelfde doen.
6. De aliaslaag-correcties uit PEIL_MIGRATIE §8.1 in de generator (sinds stap 2 open).

---

## 9. Bestanden en opnieuw meten

- Samenvattingen: [`docs/screenshots/peil/review/`](../screenshots/peil/review/) (`ronde-1-samenvatting.json`,
  `ronde-2-samenvatting.json`, `ronde-3-samenvatting.json`, uitsneden voor en na per bevinding).
- Afdrukken na de review: `docs/screenshots/peil/na/web/` — de schermen die de review veranderde zijn vervangen (zelfde
  namen), plus `verlichting-offline-nooit-geladen-later-*`.
- Scripts: `docs/screenshots/peil/werkwijze/review.mjs` en `analyse.py` (alleen documentatie; draaien buiten de repo
  met `PEIL_SCRIPT=review.mjs ./run_shots.sh <map>`, zie `werkwijze/README.md`).

---

## 10. QA-controle na de review (stap 6, 9 november 2026)

Onafhankelijke controle van de hele Peil-ronde op regressies, tests en eerlijkheid. Gemeten op HEAD `daeb9d3` en
daarna op de reparaties hieronder.

- **Geen functionele regressie.** De OpenAPI-export is byte-gelijk aan `docs/openapi/my_house.openapi.json`; dat bestand is
  sinds `210614b` (vóór de ronde) niet veranderd. Routes, API-paden in de web-app, `data-testid`'s en rollen zijn gelijk
  of alleen aangevuld (3 nieuwe testid's, 2 extra `role`'s). `apps/api/src` veranderde alleen in migratie 0004 (stap 1,
  ADR-0007); `integrations/`, compose, `install.sh` en `render_truenas.py` niet. Versie 0.2.0, geen tag, geen release.
- **Gerepareerd:**
  1. De offline-browsertest (`08-offline`) controleerde "nooit groen offline", maar zonder worker stond er online geen
     enkele "Gezond" op het overzicht: los gedraaid was de controle altijd waar. De test zet nu een verse hartslag van
     de achtergronddienst (testgegevens via `seed.py`) en eist online ≥ 1 Gezond en offline evenveel Verouderd. Tegen
     een build zonder de offline-regel (ADR-0011) faalt hij nu (`Expected: 0, Received: 1`).
  2. Het startscript van de webcontainer liet verkorte schrijfwijzen van loopback door naar `/_shc/config.json`
     (`0177.0.0.1`, `2130706433`, `0x7f.1`, `[0::1]`, `[0:0:0:0:0:ffff:7f00:1]`). De browser verborg de link al
     (`src/hub.ts` gebruikt de URL-parser), maar het logboek zweeg. Nu: IPv4 alleen als `a.b.c.d`, IPv6-loopback en `::`
     in elke schrijfwijze geweigerd; getest met de host-shell én in de image (busybox).
  3. OpenAPI-driftcontrole als pytest (`apps/api/tests/test_openapi_snapshot.py`, contract §5): een
     alleen-uiterlijk-ronde blijft zo aantoonbaar zonder API-wijziging.
  4. Documentatie: de README zei nog "nog niet gebouwd"; de Android-README noemde `Theme.kt` als bron van de kleuren;
     `thema.js` verwees naar een testbestand dat niet bestaat; HERKOMST.md vergelijkt nu ook met de bron in
     ai-server-manager (`172ee87`).
- **Niet veranderd, wel gemeld:** `/_shc/config.json` is zonder inloggen leesbaar. Via een tunnel zonder Cloudflare
  Access ziet een buitenstaander dus het (interne) Hub-adres. Contract 1.2 noemt `HUB_URL` niet geheim, maar het blijft
  een keuze voor de gebruiker (ADR-0008 punt 3).
- Echte apparaten, een schermlezer en Android op toestel of emulator: nog steeds **NIET GEVALIDEERD (hardware)**.

## 11. Peil 1.1.1 (10 november 2026, conformiteitsronde contract 1.3)

my_house neemt Peil **1.1.1** over via de nieuwe gepinde kopie (`design/ontwerptaal/HERKOMST.json`, bron-commit
`4ff4b3f`, ADR-0012), niet door iets lokaal te omzeilen. Wat er in de app veranderde: de `-soft`-banden van verwarmen en
koelen, de rand van een gekozen modus en een brandende lichttegel in `-ink`, de ondernavigatie op labelbreedte zonder
vet actief item (web en Android), en de arcering van overgenomen schemadelen uit de aliaslaag. Functies, routes,
teksten en API zijn gelijk (OpenAPI-driftcontrole groen).

**Gemeten** met `docs/screenshots/peil/werkwijze/review.mjs` (zelfde methode als §5.2; echte stack, fictieve gegevens):
28 schermen en toestanden × 320/360/390/768/1024/1440 × licht/donker = 332 metingen, plus een volledige Tab-doorloop op
390 en 1440 px. Samenvatting: `docs/screenshots/peil/review/peil-1.1.1-samenvatting.json`.

- **axe-core 4.14** (WCAG 2.0/2.1/2.2 A+AA): **0** overtredingen.
- **Tekstcontrast in de pagina:** 0 onder 4,5:1; laagste 4,56:1 (de bewust gedempte laatst bekende waarde, zoals in
  §5.2). Tussen 4,5 en 7:1 staan alleen nog de `stale-ink`-teksten: de labels in de schemaband ("Dag 21,0°",
  "Nacht 18,0°") staan er **niet meer** in (1.1.0: 4,93–6,22:1; nu ≥ 7:1, `-ink` op de nieuwe `-soft`).
- **Inhoud buiten zijn element:** **0** (1.1.0: "Overzicht" in de ondernavigatie stak op 320 px 2 px uit zijn kolom,
  `ronde-3-samenvatting.json`). De browsertest telt de ondernavigatie nu mee.
- **UI-grenzen:** de 4 meldingen onder 3:1 zijn dezelfde als in 1.1.0: de hovertoestand van "Terug naar de Hub" (§5.2,
  geen fout).
- **Overloop** 0 px, **doelen** < 44 px 0, **focus**: elke stop een ring (laagste 7,55:1), niets helemaal bedekt.
- **Browsertests** (`apps/web/e2e/run-stack.sh`, 26 tests): axe 0 op de hoofdschermen in licht en donker op 390 en
  1440 px, de Peil-matrix 320–1440 px, de Tab-doorloop en de nieuwe test "Peil 1.1.1" (`-soft` ≥ 7:1 met `-ink`,
  `-ink`-rand bij een gekozen modus en een brandende lichttegel, ondernavigatie zonder extra gewicht en met precies één
  `aria-current`). Tegen de recepten van 1.1.0 faalt die test aantoonbaar (rand `rgb(235, 104, 52)` in plaats van
  `rgb(110, 36, 5)`; "Overzicht" 2 px buiten zijn element).
- **Android:** 40 van de 56 Paparazzi-afdrukken opnieuw vastgelegd (ring met de nieuwe `-soft`, ondernavigatie zonder
  vet); de 16 zonder ondernavigatie bleven byte-gelijk. `PeilContrastTest` en `PeilTokensTest` groen.

Afdrukken: `docs/screenshots/peil/review/11-peil-1.1.1-modus-en-schemaband-{licht,donker}-390.png` en
`11-peil-1.1.1-ondernavigatie-{licht,donker}-360.png`.

**NIET GEVALIDEERD:** echte schermlezer (TalkBack/NVDA), echte telefoon in fel licht, Android op toestel of emulator en
de app achter een echte TrueNAS. Alles hierboven is Sim-getest in Chromium (Playwright) en layoutlib (Paparazzi).

## 12. Grafiek "Laatste 24 uur" met tabelweergave (11 november 2026, onafhankelijke ontwerpreview)

**Rol:** ontwerp-agent, onafhankelijk van de bouwer van de tabel en de toetsenbediening (ADR-0013) · **Maatstaf:** Peil
1.1.1 §10 (grafieken) en COMPONENTEN.md "Lijngrafiek" (`design/ontwerptaal/`), het Peil-prototype
`server-metrieken.html` met zijn afdrukken, en WCAG 2.2 AA · **Besluit bij deze review:**
[ADR-0014](../adr/ADR-0014-grafiek-24-uur-ontwerpreview-verouderd-en-figuur.md).

Vaste regels: alleen uiterlijk, teksten en toegankelijkheid. Geen nieuwe API, geen nieuw datamodel, geen nieuwe route
of instelling; de grafiek leest dezelfde twee antwoorden als voorheen (`GET /zones/{id}/history` en de zone zelf).

### 12.1 Oordeel in het kort

De tabel per uur, de toetsenbediening, de leesregel en de eerlijke gaten uit ADR-0013 waren goed: axe-core 0, geen
contrastfout, geen overloop, geen te klein doel, een zichtbare ring. De nulmeting vond wel **zeven punten**: één
eerlijkheidsfout, drie afwijkingen van Peil §10 (figuur met bron en tijd, eindpunt, tijdas) en drie kleine (aslabels,
legenda, een dubbele tijd). Tijdens het repareren kwamen er nog vier bij. Alle elf zijn gerepareerd (§12.4).

- **De ernstigste:** een sensor die de server **verouderd** noemt, stond in de grafiek als een gewone lijn die bij de
  laatste meting ophield — geen arcering, geen woord, geen tijd. De grafiek zag eruit als "actueel".
- **Na de reparatie** (100 combinaties van toestand × breedte × thema): axe-core **0**; tekst ≥ **10,68:1**;
  lijnen, randen, eindpunt en ring ≥ **4,42:1**; niets afgekapt, niets buiten zijn vlak, geen overloop, geen doel
  onder 44 px; het plotvak, de uitleg en de leesregel worden na Tab niet bedekt door de ondernavigatie.
- **Eén punt blijft bij Peil liggen:** de arcering (`--chart-gap`) haalt als vlak 1,55:1 (licht) en 1,58:1 (donker).
  Zij is nergens het enige teken (§12.6), maar het token hoort bij de bron (§12.7).

### 12.2 Werkwijze

- **Echte dienst, lokaal:** `apps/web/e2e/run-stack.sh` start de echte API, PostgreSQL 16 en de gebouwde web-app;
  de metingen komen via `e2e/seed.py readings` in de database zoals de worker ze opslaat. Er is geen echte airco en
  geen echte sensor: alles hieronder is **Sim-getest**.
- **Tien toestanden:** vol (24 uur), gat (zes uur met een gat van 2½ uur en een meting zonder waarde), verouderd
  (laatste meting ruim drie uur oud; de server noemt de sensor verouderd), kort (acht minuten), enkel (één meting),
  ijl (een sensor die één keer per uur meldt, zonder doel), geen (alleen metingen zonder waarde), leeg (geen
  geschiedenis), en na de reparatie erbij: oudkort (net verouderd) en ouddoel (sensor verouderd terwijl het doel nog
  binnenkomt).
- **Matrix:** 320 · 360 · 390 · 768 · 1440 px × licht en donker. Nulmeting 8 × 5 × 2 = **80** combinaties (commit
  `a3acb18`), eindmeting 10 × 5 × 2 = **100**. Per combinatie drie standen: rust, focus op het plotvak, en open tabel
  met een gekozen uur; daarnaast Tab vanaf de bovenkant van de pagina.
- **Gemeten per combinatie** (Playwright 1.63, Chromium, axe-core 4.14 met wcag2a/2aa/21a/21aa/22aa): overtredingen;
  contrast van elk tekstelement en van elke lijn, rand, stip en ring op de berekende kleuren (arcering en pil tegen de
  donkerste pixel); zijwaartse overloop; tekst onder 14 px; doelen onder 44 px; inhoud buiten de kaart of buiten zijn
  element; labels die elkaar raken of buiten het plotvak steken; de ring (dikte, contrast, bedekt of niet).
- **Bekeken:** van elke combinatie een afdruk, als contactvellen naast het Peil-prototype en de Peil-afdrukken.
- De meetspec was tijdelijk en staat niet in de repo. Wat blijvend bewaakt wordt, staat in de browsertest
  `apps/web/e2e/12-grafiek-24-uur.e2e.ts` en in `src/test/history-chart.test.tsx` (§12.9).

### 12.3 Vergelijking met de Peil-regels en het prototype

| Peil 1.1.1 | Nulmeting | Na de review |
|---|---|---|
| §10 "**Verouderd:** de lijn stopt bij het laatste punt; daarna arcering met 'Verouderd sinds 10:12'" | ontbrak: de as hield op bij de laatste meting | lijn stopt, eindpunt, gearceerd stuk met stippelrand tot nu, "Verouderd sinds 06:28" als het past, pil en tekst eronder |
| §10 "`<figure>` + `figcaption` met bron en tijd" | losse `div`, geen bron, geen tijd | `<figure>`; bijschrift "Bron: sensor van de airco · bijgewerkt 11 nov 08:29 · …" |
| §10 "**'Toon als tabel'** altijd" | aanwezig (ADR-0013) | ongewijzigd; staat ná de figuur (§12.7 punt 1) |
| §10 "**Gat** … `arcering-gat` met het label 'Geen meting'" | aanwezig (ADR-0013) | ongewijzigd; een gat dat doorloopt in het verouderde stuk wordt niet dubbel gearceerd |
| COMPONENTEN "eindpunt 12 px met `ring-2 ring-card` + direct label" | geen eindpunt | 12 px met kaartring op elke breedte; waarde ernaast vanaf 34 rem bij een verse sensor |
| COMPONENTEN "x-labels: eerste links, laatste rechts uitgelijnd, tussenlabels pas vanaf `sm`" | alleen begin en eind, ook op 1440 px | begin en eind; hele uren ertussen vanaf 34 rem |
| COMPONENTEN "`figcaption`: bron · bijgewerkt · 'een gat betekent dat er geen meting was (nooit 0)'" | alleen "Gearceerd: geen meting (nooit 0)." bij een gat | bron · bijgewerkt · de gat-zin (of, als er iets gearceerd is, de arceer-uitleg met staaltje) |
| §7 reekskleur `chart-1`, grenslijn `chart-limit`, raster `chart-grid` | juist | ongewijzigd |
| §10 toetsenbord, `aria-live`, één Tab-stop | juist (ADR-0013) | ongewijzigd |

**Wat het prototype anders doet, en waarom my_house dat niet overneemt:** het prototype zet het eindlabel en het
verouderd-label over de gegevens heen (op 390 px bedekt het label het laatste stuk van de lijn) en zet `<details>` na
`<figcaption>` binnen `<figure>`. my_house toont beide labels alleen waar ze passen en zet de tabel na de figuur
(ADR-0014 punt 1, 4 en 5).

### 12.4 Bevindingen en reparaties

| # | Bevinding | Ernst | Reparatie | Bewaakt door |
|---|---|---|---|---|
| 1 | Verouderde sensor zag eruit als actueel: geen arcering, woord of tijd in of onder de grafiek; de toegankelijke naam zei alleen "laatste meting 20,5°" | hoog (eerlijkheid) | het oordeel van de server (`zone.temperature.stale`) stuurt de markering: lijn stopt, gearceerd stuk met stippelrand in `border-stale`, "Verouderd sinds …" als het past, pil "Verouderd" (icoon + woord + stippelvorm) met "Laatst bekend 11 nov 06:28: 20,1°. Daarna kwam er geen meting meer.", naam eindigt op ", verouderd sinds 11 nov 06:28", de uren erna heten "Geen meting" | vitest; browsertest "verouderde sensor" (licht/donker × 390/1440) |
| 2 | Geen `<figure>`/`figcaption`, geen bron, geen tijd | middel | `<figure>` met bijschrift: bron ("sensor van de zone" / "sensor van de airco" / "temperatuursensor") en "bijgewerkt" in `<time datetime>`; bij verouderd geen "bijgewerkt" (zou "Verouderd" tegenspreken) | vitest; browsertest |
| 3 | Geen eindpunt bij de laatste meting | middel | stip van 12 px met ring van 2 px in de kaartkleur; waarde ernaast in een eigen strook vanaf 34 rem, alleen bij een verse sensor | vitest; browsertest (maat en plaats) |
| 4 | Tijdas met alleen begin en eind, ook op 1440 px | middel | hele klokuren ertussen vanaf 34 rem (stap 1, 2, 3, 6 of 12 uur; niet binnen 15 % van begin of eind) | vitest (`hourTicks`); browsertest (labels raken elkaar niet) |
| 5 | y-labels 4,8 px boven en onder het plotvak; de focusring liep door "17°" en "22°" | laag | 6 px ruimte in het plotvak boven en onder; labels vallen erbinnen | browsertest (labels binnen het plotvak) |
| 6 | Legenda ook boven "Nog geen geschiedenis"; staaltjes van 12 px (het streepje van "Doel" was één blokje) | laag | legenda alleen als er iets te tekenen is; staaltjes 20 px | `peil-review.test.tsx` |
| 7 | Eén meting: dezelfde tijd stond links én rechts onder de as ("07:38  07:38") | laag | bij een gelijk begin en eind één label | vitest |
| 8 | *(tijdens de reparatie)* De doellijn liep tot een uur het verouderde stuk in | middel (eerlijkheid) | de doellijn stopt bij de grens, of bij haar eigen laatste waarde als het doel nog wel binnenkomt (ADR-0014 punt 3) | vitest (2 tests; zonder de regel falen ze allebei); browsertest (einde doellijn = begin arcering ± 1 px) |
| 9 | *(tijdens de reparatie)* "Verouderd sinds …" brak in twee of drie regels terwijl er ruimte was | laag | label pas vanaf een stuk van 9 rem, breedte tot 12 rem | browsertest (1440: binnen het stuk, één regel; 390: afwezig) |
| 10 | *(tijdens de reparatie)* De stippelrand werd een kader aan vier kanten (geen Tailwind-preflight) | laag | `border-0 border-l-2 border-dashed` (ADR-0014 punt 9) | afdrukken; meting "verouderd rand" |
| 11 | *(tijdens de reparatie)* "11 nov" en "08:29" braken uit elkaar in het bijschrift; de ring raakte de tijdlabels | laag | `time { white-space: nowrap }`; tijdas 6 px onder het plotvak | afdrukken; meting afkappen/overlappen = 0 |

### 12.5 Controle per eis

| Eis | Nulmeting (80) | Na de review (100) |
|---|---|---|
| axe-core (WCAG 2.2 AA) | 0 | **0** (rust en open tabel) |
| Tekstcontrast ≥ 4,5:1 | laagste 10,83 (licht) / 10,68 (donker) | laagste **10,83 / 10,68**; hoogste 19,22 / 16,13; kleinste letter 14 px |
| Meetlijn, eindpunt, losse stip, legenda "Gemeten" (`chart-1`) ≥ 3:1 | 4,42 / 4,85 (geen eindpunt) | **4,42 / 4,85** |
| Doellijn en legenda "Doel" (`chart-limit`, 1,5 px, streep 6/4) | 19,22 / 16,13 | **19,22 / 16,13** |
| Rand van het verouderde stuk (`border-stale`, 2 px gestippeld) | bestond niet | **6,36 / 7,78** |
| Pil "Verouderd": rand; tekst op de donkerste arceerpixel; maat | bestond niet | **6,36 / 7,78**; **8,97 / 9,24**; 121 × 32 px (geen knop) |
| Draadkruis (randen in inkt), chevron van "Toon als tabel", rand van het arceer-staaltje | 19,22 / 16,13; 19,22 / 16,13; 5,89 / 6,11 | gelijk |
| Focusring plotvak: 3 px, 2 px afstand | 9,45 / 8,63, niet bedekt | **9,45 / 8,63**, niet bedekt (40 + 40) |
| Tab-stops (plotvak en "Toon als tabel") | ring 3 px, laagste 8,63, 0 bedekt | ring 3 px, laagste **8,63**, **0** bedekt (160 stops) |
| Tab vanaf boven: plotvak, uitleg en leesregel boven de ondernavigatie | niet bedekt | **niet bedekt** op alle vijf breedtes |
| Zijwaartse overloop | 0 px | **0 px** |
| Aanraakdoelen ≥ 44 px | 0 te klein | **0** te klein ("Toon als tabel" is 44 px hoog) |
| Tekst onder 14 px; inhoud buiten kaart of element | 0; 0 | **0; 0** |
| Labels afgekapt, over elkaar of buiten het plotvak | 360 meldingen (y-labels 4,8 px buiten het plotvak) | **0** |
| Verouderd/onbekend nooit groen | niets groen, maar verouderd ook niet gemarkeerd | niets groen; verouderd = grijs gearceerd met stippelrand, pil, tekst en naam |
| Status nooit alleen kleur | reeksen: doorgetrokken en gestreept + legenda; gat: arcering + woord + uitleg | hetzelfde, plus verouderd: icoon + woord + stippelvorm + zin + naam |

**Onder 3:1, en waarom dat geen fout is:** de arcering (1,55 / 1,58; aanvullend teken, zie §12.6), de rasterlijnen
(1,25 / 1,29; Peil §7: raster treedt terug), het vlak van het draadkruis (1,18 / 1,22; de randen ervan zijn inkt) en het
vlak van de tabelkop (1,23 / 1,14; de kop is vet en heeft een lijn eronder). Geen daarvan is een grens die de lezer
nodig heeft om een onderdeel te vinden of een toestand te herkennen.

**Eindpunt, label en tijdas per breedte** (licht en donker gelijk): het eindpunt is overal 12 px. De waarde ernaast
staat op 768 en 1440 px bij een verse sensor en ontbreekt op 320–390 px en bij een verouderde sensor. Tijdlabels: 2 op
320–390 px, 6 of 7 op 768 en 1440 px (24 uur), 2 bij acht minuten, 1 bij één meting.

**Het verouderde stuk per breedte:** ruim drie uur verouderd op 24 uur: 32 · 38 · 43 · 103 · 164 px (label alleen op
1440); net verouderd: 7 · 8 · 9 · 23 · 36 px (nooit een label); zes uur verouderd: 51 · 61 · 69 · 165 · 262 px (label
op 768 en 1440). Zonder label staan de tijd en de laatst bekende waarde in de pilregel onder de grafiek en in de
toegankelijke naam — op elke breedte.

**NL-microcopy tegen de Peil-woordenlijst (§11):** "Verouderd" (niet: stale, oud), "Verouderd sinds 06:28" (§10
letterlijk), "Laatst bekend …" (§4.4), "Geen meting" (§10; nooit 0, nooit "–"), "Bron" en "bijgewerkt" (§4.4), "Toon als
tabel, per uur" (§10 met de eenheid van de rijen erbij). Geen Engels, geen jargon, geen uitroeptekens. Nieuwe zinnen:
"Daarna kwam er geen meting meer." en "geen gemeten temperatuur in de laatste 24 uur" (bronregel als alleen het doel
bekend is).

### 12.6 Niet gerepareerd of bewust gelaten

| Punt | Waarom | Voorstel |
|---|---|---|
| Arcering `--chart-gap` 1,55:1 / 1,58:1 tegen de kaart | Peil-token; de kopie is gepind. Nooit het enige teken: een gat heeft het woord (als het past) en de uitleg met staaltje (rand 5,89 / 6,11:1); het verouderde stuk heeft de stippelrand, de pil en de zin | §12.7 punt 3 |
| Geen "Verouderd sinds …" in een stuk smaller dan 9 rem (telefoon; kort verouderd) | het label past er niet in zonder afkappen of over de lijn te vallen | tijd en waarde staan in de pilregel en de naam; geen |
| Geen waarde naast het eindpunt onder 34 rem | het label zou het eind van de lijn bedekken (zoals in het prototype op 390 px) | de waarde staat groot in het bedieningsblok erboven en in de naam; §12.7 punt 2 |
| Lege strook onder de tijdas op een smal venster op een computer | de vrijgehouden plaats voor de leesregel bij een muis (ADR-0013 punt 4); op een echt aanraakscherm bestaat zij niet | geen |
| Teksten, tabel en toetsen van ADR-0013; offline (ADR-0011) | gecontroleerd, in orde | geen |
| Volledige afdrukken `klimaat-bediening-*` in `docs/screenshots/peil/na/` | tonen nog de grafiek van vóór ADR-0013; de sectie zelf staat in `review/12-grafiek-24-uur-*` | bij een volgende volledige afdrukronde vervangen |

### 12.7 Voorstellen voor Peil (bron: ai-server-manager)

1. Prototype `server-metrieken.html`: `<details>` staat na `<figcaption>` binnen `<figure>`; HTML staat `figcaption`
   alleen als eerste of laatste kind toe. Zet de tabel na de figuur, of het bijschrift onderaan.
2. Regel voor het eindlabel en het verouderd-label: "alleen als het past; anders in het bijschrift en de
   toegankelijke naam". Het prototype zet ze nu over de gegevens.
3. `--chart-gap` ophogen tot ≥ 3:1, of een rand om elk gearceerd vlak voorschrijven.
4. §10 zegt niet wat een doel- of grenslijn doet in een verouderd stuk. my_house: stoppen bij de grens, tenzij de
   lijn zelf nog gegevens heeft.
5. Geen ontwerpvoorstel maar een API-wens: een servertijd (`as_of`) in het history-antwoord, zodat de breedte van het
   verouderde stuk niet van de klok van het apparaat afhangt (ADR-0014 punt 2).

### 12.8 Niet getest (NIET GEVALIDEERD)

- **Echte schermlezer** (NVDA, JAWS, TalkBack, VoiceOver): niet beschikbaar. Naam, rol, beschrijving en `aria-live`
  zijn automatisch gecontroleerd, niet beluisterd.
- **Echte telefoon of tablet, fel licht:** **NIET GEVALIDEERD (hardware)**. De tik is getest op het nagebootste
  aanraakscherm van de testbrowser.
- **Firefox en Safari:** niet getest; alleen Chromium. De containerquery's (`@container`) vragen Safari 16+,
  Firefox 110+ en Chrome 105+; dat ligt onder wat Tailwind 4 al vraagt.
- **Een echte airco of sensor:** de reeksen zijn testgegevens in het formaat van de worker. Hoe vaak een echte
  sensor in de praktijk verouderd raakt, is onbekend.
- **Een scheve apparaatklok** is alleen in de unit-tests nagebootst (grenzen van het verouderde stuk), niet in de
  browser.
- **200 %-zoom** is benaderd met de breedtes 320–390 px, niet met echte browserzoom.

### 12.9 Bestanden en opnieuw meten

- Metingen: [`docs/screenshots/peil/review/12-grafiek-24-uur-samenvatting.json`](../screenshots/peil/review/12-grafiek-24-uur-samenvatting.json)
  (nulmeting en eindmeting naast elkaar).
- Afdrukken: `docs/screenshots/peil/review/12-grafiek-24-uur-<licht|donker>-<390|1440>.png` (gat, gekozen uur, open
  tabel) en `12-grafiek-24-uur-verouderd-<licht|donker>-<390|1440>.png` (verouderde sensor, rust).
- Opnieuw meten: `SHC_E2E_SHOTS=<map> apps/web/e2e/run-stack.sh` draait de browsertests en bewaart dezelfde acht
  afdrukken. Flow `12-grafiek-24-uur` (6 tests) meet axe-core, de ring, overloop, tekst- en doelmaten en inhoud buiten
  zijn vlak op 320–1440 px in licht en donker, en de verouderde sensor op 390 en 1440 px.
- Gedraaid op de eindstand: `npx tsc --noEmit` en `npx tsc -p e2e/tsconfig.json` zonder fouten; vitest **332**
  geslaagd (28 bestanden; 38 in `history-chart.test.tsx`, was 19); `npm run build` in orde; browsertests **31 van 31**
  geslaagd (was 30). Eerlijk over de weg ernaartoe: één tussentijdse volledige ronde had 1 mislukte test van de 31 —
  de nieuwe controle op het einde van de doellijn mat de omtrek van het SVG-pad, en die is ruimer dan de lijn zelf
  (261,3 tegen 260,3 px). De controle rekent nu met de coördinaten van het pad; de ronde daarna slaagde volledig.
