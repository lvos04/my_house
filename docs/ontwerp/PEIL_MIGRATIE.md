# my_house naar Peil — omzettingsontwerp (stap 2)

**Status:** web-app gebouwd (fase 1 en fase 2 + beide toevoegingen, stap 3 — zie [§14](#14-uitvoering-web-stap-3)); Android-app gebouwd (stap 4 — zie [§15](#15-uitvoering-android-stap-4)) · **Peil-versie:** 1.1.0 (gepinde kopie in [`design/ontwerptaal/`](../../design/ontwerptaal/HERKOMST.md)) ·
**Datum:** 9 november 2026 · **Rol:** design-agent (contract §13: ontwerpstap vóór de UI-bouwstap) ·
**Afdrukken vooraf:** [`docs/screenshots/peil/voor/`](../screenshots/peil/README.md) · **Besluit:** [ADR-0008](../adr/ADR-0008-peil-omzetting-twee-fasen.md)

Dit document vertaalt elk scherm en elk onderdeel van de my_house-web-app (`apps/web`, Vite 5 + React 18 PWA, eigen CSS
"Rustig huis") en de native Android-app (`apps/android`, Kotlin + Jetpack Compose) naar Peil. Het bouwt **niets** aan
de app. Het legt vast wat de bouwer in fase 1 en fase 2 doet, wat er niet mag veranderen en welke tests geraakt worden.

Inhoud: [1 Uitgangspunten](#1-uitgangspunten-wat-verandert-en-wat-niet) · [2 Nulmeting](#2-nulmeting-voor) ·
[3 Inventaris](#3-inventaris-van-schermen-en-toestanden) · [4 Tokens](#4-tokens-web) ·
[5 Componenten](#5-componenten-web) · [6 Per scherm](#6-per-scherm-web) · [7 Grafieken](#7-grafieken-dataviz) ·
[8 Fasen](#8-de-twee-fasen-web) · [9 Toevoegingen](#9-toegestane-toevoegingen) · [10 Microcopy](#10-microcopy-nl) ·
[11 Android](#11-android-peil-in-jetpack-compose) · [12 Tests](#12-welke-tests-geraakt-worden) ·
[13 Open punten](#13-risicos-open-punten-en-contractpunten) · [14 Uitvoering web](#14-uitvoering-web-stap-3) ·
[15 Uitvoering Android](#15-uitvoering-android-stap-4)

---

## 1. Uitgangspunten: wat verandert en wat niet

**Vast (verandert in geen enkele fase):** routes (`/`, `/klimaat`, `/verlichting`, `/energie`, `/optimalisatie`,
`/apparaten`, `/systeem`, `/account`, `/installatie`, `/help`), de queryparameters (`?zone=…&tab=…`) en hashes
(`/account#telefoons`, `/systeem#adressen`), de API-koppeling en de OpenAPI (`docs/openapi/my_house.openapi.json` blijft
byte-gelijk), het foutformaat, het datamodel, de poorten en de installatie. Functies en gebruikersreizen blijven, ook de
offline-regel (3 mislukkingen of 15 s), het snapshotgedrag, de readback van commando's, de boilerblokkade op drie
plekken, de rollen beheerder/kijker en telefoonrollen. Bestaande `data-testid`'s, ARIA-rollen, toegankelijke namen en
de **haakklassen** die tests gebruiken blijven bestaan (lijst in §12). Integriteit, rollback en monitoring worden niet
aangeraakt.

**Wat verandert:** alleen het uiterlijk — tokens (kleur, lettertype, radius, randen), componentstijl, iconen
(lucide), de vorm van statusweergave (icoon + woord + vorm), focusring, minimale lettergrootte en aanraakdoelen.
Statuswoorden worden de vaste Peil-woorden waar ze status zijn (§10); teksten met domeinbetekenis blijven.

**Toegestaan als toevoeging (besluit gebruiker, BESLUITEN_GEBRUIKER.md):** de link **"Terug naar de Hub"** (O-11) en de
**themaknop Systeem / Licht / Donker** (O-09), beide in de web-app (§9). Ook de **Android-app** krijgt de Peil-stijl
(§11). my_house fase 2 gebeurt op **Tailwind 4 + shadcn/ui in de React 18-variant (forwardRef)**; geen React 19-upgrade.

**Niet in deze omzetting:** een tabelweergave bij de temperatuurgrafiek "Laatste 24 uur" (de tabel-toevoeging is alleen
voor MediaGuard/MSO besloten; voor my_house is het een aanbeveling, zie §13), een themaknop of Hub-link in de
Android-app, en nieuwe functies. *Later toegevoegd:* de gebruiker heeft de tabel en de bediening met het toetsenbord
bij "Laatste 24 uur" op 10 november 2026 toegestaan; gebouwd op 11 november 2026, zie §18.

---

## 2. Nulmeting "voor"

### 2.1 Schermafdrukken

- **Web:** 222 afdrukken van 28 schermen/toestanden × 360/390/768/1440 px × licht/donker (de Meer-sheet alleen < 820 px),
  tegen de **echte app** (API + PostgreSQL + gebouwde web-app) met fictieve testgegevens. Geen horizontale overloop
  gemeten op enige breedte (`voor/web/index.json`, veld `overloop_px`). Op de 8 afdrukken van de installatiewizard is
  één echt LAN-adres van de testmachine (antwoord van de netwerkdetectie) met een magenta vak afgedekt.
- **Android:** 68 afdrukken van 11 schermen/toestanden × 360/393 dp × licht/donker, met Paparazzi in een kopie buiten de
  repo (de app heeft geen screenshot-tests; geen emulator bruikbaar). Echte composables, echte API-gegevens.
  **NIET GEVALIDEERD (hardware).**
- Werkwijze, testgegevens en beperkingen: [`docs/screenshots/peil/README.md`](../screenshots/peil/README.md).

### 2.2 Toegankelijkheid (axe-core en eigen metingen)

Gemeten op 9 nov 2026 tegen de echte app met dezelfde testgegevens: 13 schermen × 390/1440 px × licht/donker = 52
metingen, axe-core 4.14 met de tags `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa`, plus eigen DOM-metingen. Ruwe
uitkomst: [`voor/web/axe-voor.json`](../screenshots/peil/voor/web/axe-voor.json).

| Meting | Uitkomst "voor" | Peil-eis | Opgelost in |
|---|---|---|---|
| axe WCAG A/AA | **1 regel**: `label-content-name-mismatch` (ernst *serious*, WCAG 2.5.3: de toegankelijke naam bevat de zichtbare tekst niet). Overzicht: 5 elementen — de lichttegels (zichtbaar "Keuken Uit", naam "Keuken: Uit. Tik om aan te zetten") en de knoppen "Gezien" (naam "Melding … afhandelen"). Verlichting: 3 — de aan/uit-knop per groep (zichtbaar "Uit", naam "Keuken aanzetten"). Op beide breedtes en in beide thema's; de andere 11 schermen: 0 | 0 | fase 2, zie §5.2 (LichtTegel, Gezien) |
| Tekst kleiner dan 14 px | op elk scherm; samen 2 456 tekstknopen van 13 px, 290 van 12 px en 24 van 11 px over de 52 metingen | ≥ 14 px | fase 1 alleen `.q`; verder fase 2 (§8.1 laatste rij) |
| Bedienbare doelen < 44 px | `summary` 32 px hoog (uitklapblokken op Help, Systeem, Energie, Installatie), losse links `a.small` 19 px hoog ("Alle instellingen", "Energie"), dagtabs van het weekschema 41 × 44 px op 390 | 44 × 44 overal | fase 2 |
| Elementen met opacity en tekst | 0–8 per scherm (o.a. Klimaat met geblokkeerde modi 7, Systeem 8) | 0 | fase 1 deels (aliaslaag), rest §8.1 |
| Horizontale overloop | 0 px op alle 222 afdrukken en 52 metingen | 0 | blijft bewaakt (e2e `10-mobile` + Peil-matrix) |

Niet gemeten en dus open: Tab-doorloop met bedekte focus, forced colors, 200 %-zoom, echte schermlezer
(NVDA/TalkBack) en een echte telefoon in fel licht.

### 2.3 Contrast en kleur (berekend uit de CSS/Kotlin-waarden)

| Paar (huidig) | Licht | Donker | Eis Peil | Wat Peil ervan maakt |
|---|---|---|---|---|
| `--border` op kaart (kaarten, lichttegel, tabelregels) | 1,30:1 | 1,30:1 | decoratief; bedienbaar ≥ 3:1 | kaart `border` (2,0:1, decoratief); lichttegel `border-strong` 5,89:1 |
| `--border-strong` (rand van `.btn`) op kaart | 1,50:1 | 1,80:1 | ≥ 3:1 | `border-strong` 5,89:1 / 6,11:1 |
| `--input-border` op kaart | 4,21:1 | — | ≥ 3:1 | `input` 5,89:1 / 6,11:1 |
| `.q.measured` groen op groen vlak, 12 px | 4,65:1 | — | geen groen voor meetkwaliteit; ≥ 14 px | neutraal label `· gemeten` (`muted-foreground` op `sunken`), 14 px |
| `.pill.heat` oranje tekst op oranje vlak | 4,62:1 | 6,13:1 | tekst ≥ 7:1 | `domain-heat-ink` op `domain-heat-bg` 9,44:1 / 11,01:1 |
| `.light-tile:disabled` (opacity 0,6) | ± 2,7:1 | — | geen opacity op tekst | volle tekst + gestippelde `unknown`-rand |
| Android `outline` op kaart | 1,30:1 | 1,42:1 | decoratief; bedienbaar ≥ 3:1 | `border` / `border-strong` |
| Android `controlOutline` op kaart | 3,40:1 | — | ≥ 3:1 | `border-strong` 5,89:1 |
| Primaire knop (bijna-zwart, tekst wit) | 17,2:1 | — | blauw = interactie | `primary` #123DA3, tekst 9,45:1 / 9,32:1 |

**Domeinkleuren (dataviz-validator en OKLab-afstand, zelf gedraaid):**
- Verwarmen `#B9470F` en boiler `#C26A2E` liggen ΔE **7,8** (licht) en **2,8** (donker) uit elkaar: in donker zijn
  "verwarmen" en "boiler" niet te onderscheiden. Peil: boiler = `domain-boiler` (aqua, grafiekslot 3), ΔE 27,7 van verwarmen.
- Het huidige donkere grafiekpaar airco `#86AEEB` / boiler `#E0905A` **faalt** de validator (lichtheidsband en
  chromavloer). Peil `chart-1` `#3987e5` + `chart-3` `#199e70` op `#141920`: **alle controles PASS** (CVD ΔE 19,6,
  normaal 20,9, contrast ≥ 3:1). Licht `#2a78d6` + `#00a672` op `#FFFFFF`: PASS (CVD 21,9, normaal 23,0).
- Verwarmen/koelen (weekschema, dial): Peil `#eb6834`↔`#2a78d6` PASS (CVD 24,7) en donker `#d95926`↔`#3987e5` PASS (CVD 26,8).
- `--heat-bg` ligt ΔE 2,8 van `--warn-bg`: een verwarmen-chip lijkt op een waarschuwing. Peil lost dat op met vorm
  (geen 2 px statusrand, eigen icoon `flame`) — ONTWERPTAAL §4.1 regel 9.

### 2.4 Token- en stijlaudit (design-system)

| Categorie | Gevonden in `apps/web/src` | Peil-eis |
|---|---|---|
| Kleurwaarden | 73 hex-waarden in de CSS (twee tokenblokken in `styles.css` + `.qr-box`), 2 in TSX (QR-kleuren in `Phones.tsx`), 3 `rgba(…)` | alleen tokens; QR blijft zwart-op-wit (functioneel, uitzondering) |
| Lettermaten < 14 px | 33 declaraties (11 px ×3, 12 px ×10, 13 px ×20); klasse `.small` (13 px) 277× gebruikt | minimaal 14 px |
| Inline styles | 211× `style={{…}}` (o.a. 8× `fontSize: 16`, 2× 26, 2× 40, 1× 56) | utilities (fase 2/3) |
| Opacity op tekst/bediening | `.btn:disabled` 0,45 · `.icon-btn:disabled` 0,4 · `.sp-btn:disabled` 0,4 · `.c-choices button:disabled` 0,45 · `.light-tile:disabled` 0,6 · `.is-offline .live` 0,55 · `.from-snapshot .card` 0,6 · `.week-bar .carried` 0,75 · `.lt-map.is-off` 0,75 · `.en-col.is-running` 0,7 | nooit opacity op tekst (regel 10) |
| Lettertypes | Inter + Space Grotesk via `@fontsource` | systeemfont |
| Radius | 18 px (kaart), 14/12/10/9/6 px los | 8 px (`--radius`), 4 px risico/code, 12 px sheet, pil rond |
| Iconen | 18 eigen SVG-iconen in `components/Icons.tsx` | lucide (vaste koppeling, §5.1) |
| Breekpunt zijbalk | 820 px | Peil `lg` 1024 px (fase 2, §8.2) |
| Groen voor niet-gezondheid | `.q.measured`, `.note.ok` ("Klaar om te installeren."), `.pill.ok`/`.state-line.ok` voor "Bereikbaar", `.wizard-step.done`, `.c-status.confirmed`; Android: `RouteChip` "Thuis", `QualityLabel` gemeten | groen = alleen gemeten en goed |

**Ontwerpkritiek in het kort (design-critique):** de "Rustig huis"-taal is rustig en goed leesbaar, met sterke
punten die blijven: meetkwaliteit bij elk getal, "Kies eerst een modus" in plaats van een fout, geblokkeerde modi met
uitleg, de boilerblokkade, offline-banner met tijd, en "Toon als tabel" bij de energiegrafiek. De zwakke punten zijn
precies wat Peil oplost: (1) **randen bijna onzichtbaar** (1,3–1,8:1), waardoor knoppen en tegels in de zon wegvallen;
(2) **groen voor dingen die geen gezondheid zijn** (gemeten, bereikbaar, klaar, route "Thuis"); (3) **dimmen met
opacity** bij offline en uitgeschakeld (onleesbaar, en het lijkt alsof alles "uit" is in plaats van "verouderd");
(4) **status als gekleurde stip** zonder icoon of woord (`.dot.*`, Android `StatusDot`); (5) **verwarmen en boiler
in dezelfde oranje**; (6) primaire knop bijna-zwart, terwijl in het platform blauw "interactie" betekent; (7) tekst
van 11–13 px.

---

## 3. Inventaris van schermen en toestanden

### 3.1 Web (`apps/web`)

| Scherm (route) | Toestanden in de code | Afdruk(ken) in `voor/web/` |
|---|---|---|
| **Eerste start** (`/`, `AuthPage mode=setup`) | leeg formulier · wachtwoorden verschillen (`role=alert`) · bezig | `eerste-start-beheerder-*` |
| **Inloggen** (`AuthPage mode=login`) | formulier · fout · inloglimiet (429) | `inloggen-*` |
| **Server niet bereikbaar** (`Gate`) | "De server is niet bereikbaar…" + Opnieuw proberen · API start op (`starting`) | `server-onbereikbaar-*` |
| **Geen woning** (`NoHomeSetup`, elke route behalve systeem/account/installatie/help) | formulier "Woning aanmaken" + wizard/herstel als uitweg | `woning-aanmaken-*` |
| **Schil** (`Layout`) | zijbalk (> 820 px) · kopbalk + ondernavigatie (≤ 820) · Meer-sheet · skiplink · woningkiezer (> 1 woning) · gearchiveerde woning (alleen lezen) · "Volgende stappen" · voet met versie | elke afdruk; `meer-sheet-*` |
| **Statusregel** (`OfflineBanner`) | Cloudflare Access verlopen · offline (laatst bekend hh:mm, bediening uit) · API start · live-updates geweigerd | `overzicht-offline-*`, `klimaat-offline-*` |
| **Overzicht** (`/`) | begroeting + datum · "Let op"/"Er is een probleem met het systeem" · achtergronddienst · zonekaarten (verwarmen, uit, geen airco, onbereikbaar, handmatig, verouderd) · lichttegels (aan, uit, onbereikbaar) · Vandaag (energiebalk, meters met kwaliteit, nu-vermogen) · meldingen (Gezien) · kijker zonder bediening | `overzicht-*`, `overzicht-kijker-*`, `overzicht-verouderd-*`, `overzicht-offline-*` |
| **Klimaat › Bediening** (`/klimaat?zone=&tab=bediening`) | zonetabs · onderdeeltabs · draaiknop + −/+ · concept (cursief) · readback (wachtrij, verzonden, bevestigd, mislukt, geweigerd, time-out) · "Kies eerst een modus" · geblokkeerde modi (gedeelde buitenunit) · ventilator · Handmatig 2 uur / Terug naar schema · sensor verouderd · geen airco gekoppeld (neutraal) · Schema vandaag · Laatste 24 uur + aanpassingslog | `klimaat-bediening-*`, `klimaat-uit-conflict-*`, `klimaat-verouderd-*`, `klimaat-offline-*` |
| **Klimaat › Weekschema** | weekoverzicht (balken) · dagtabs · blokken (fouten per veld) · sjabloon · dag kopiëren · voorbeeld volgende wijziging · vaste opslaanbalk · "Niet opgeslagen wijzigingen" | `klimaat-weekschema-*` |
| **Klimaat › Comfortprofielen** | leeg ("Nog geen comfortprofielen") · lijst · nieuw profiel · toepassen | `klimaat-profielen-*` |
| **Klimaat › Instellingen** (beheerder) | zone-koppeling (unit, sensor) · regelgrenzen (formulier) | `klimaat-instellingen-*` |
| **Verlichting** (`/verlichting`) | niet-bereikbaar-melding · groepskaarten (aan/uit, helderheid, scènes) · lampen (uitklap) · knoppen (geen, koppelingen, conflict `.lt-conflict`, simuleren) | `verlichting-*`, `verlichting-offline-nooit-geladen-*` |
| **Energie** (`/energie`) | periodetabs Dag/Week/Maand/Jaar + datum · Nu (live) · meterkaarten met kwaliteit · gestapelde staven (onvolledig, lopend, toekomst, vlag) · "Toon als tabel" · Kosten · "Wat wordt gemeten" · Exporteren · Tarieven · Meetgrenzen | `energie-*` |
| **Optimalisatie** (`/optimalisatie`) | adaptief model per zone · weer meewegen · experimenten (gepland, loopt, gepauzeerd, afgerond, gestopt) | `optimalisatie-*` |
| **Apparaten** (`/apparaten`) | per categorie · apparaatkaart (getest/niet getest, online/offline, beschermd: boiler alleen meten) · rollen · relaisstatus · kalibratie · koppelen per merk (Hue-knop, HomeWizard, Gree, Zigbee-koppelvenster met aftelling) · compatibiliteitstabel · gearchiveerd | `apparaten-*` |
| **Systeem** (`/systeem`) | veilige modus · status (controles) · versie · adressen · back-ups (automatisch, nu, lijst, bewaartermijnen) · meldingen buiten de app · herstellen en verhuizen (4 stappen) · woningen · account | `systeem-*` |
| **Account** (`/account`, `#gebruikers`, `#telefoons`, `#logboek`) | eigen wachtwoord · gebruikers · telefoon koppelen (QR + code + aftelling) en lijst · logboek | `account-*` |
| **Installatiewizard** (`/installatie`) | 10 stappen (controles vooraf … klaar), optionele stappen, voortgang bewaard | `installatie-*` (stap 1) |
| **Help** (`/help`) | per merk: wat lokaal werkt, eerst in de app van de fabrikant, stap voor stap, foto volgt | `help-*` |
| **Offline, nooit geladen** | een pagina die nog niet in de cache stond, blijft op "Laden…" staan | `verlichting-offline-nooit-geladen-*` |

**Bevinding uit de afdrukken (geen ontwerpkeuze, wel relevant):** offline toont een nooit geladen pagina eindeloos
"Laden…". Peil §9 (offline): *nog nooit opgehaald = Onbekend, geen skelet*. Dat vraagt een kleine codewijziging
(laadtoestand na de offline-drempel als "Onbekend — nog niet opgehaald; je bent offline" tonen). Dat is gedrag, geen
uiterlijk; daarom als open punt in §13, niet in de omzetting.

### 3.2 Android (`apps/android`)

| Scherm | Toestanden | Afdruk(ken) in `voor/android/` |
|---|---|---|
| **Koppelen** (`PairScreen`) | stap 1 van 2 scannen · camera niet beschikbaar · handmatig invoeren · "Dit is niet jouw server" · bezig · fout · opslag gewist | `koppelen-*` (camera niet beschikbaar) |
| **Niet meer gekoppeld** (`UnpairedScreen`) | ingetrokken · verlopen · onbekend · opslag gewist · upgrade | `niet-meer-gekoppeld-*` |
| **Start** (`HomeScreen`) | route-chip + systeemregel (stip) · "Let op"-blok · zonekaarten met −/+ · lichttegels · vermogen nu · meldingenbel met teller · meldingen-uitleg · verbindingsbanner (offline, start op, verkeerde server) · kijker (alleen lezen) · gedimd bij offline (alpha 0,55) | `start-*`, `start-offline-*`, `start-kijker-*`, `start-meldingen-uitleg-*` |
| **Klimaat** (`ClimateScreen`) | zonetabs · draaiknop · −/+ · modi (geblokkeerd met uitleg) · ventilator · details · weekschema (alleen lezen, laden) · Handmatig voor 2 uur · Terug naar schema · commandostatus (wacht, opnieuw, bevestigd, mislukt, geweigerd, time-out) | `klimaat-*`, `klimaat-uit-conflict-*`, `klimaat-offline-*` |
| **Energie** (`EnergyScreen`) | Nu (balk + rijen met kwaliteit) · Verbruik vandaag (uurstaven, onvolledig gestippeld) · meterrijen · uitleg | `energie-*` |
| **Meer** (`MoreScreen`) | meldingen + Gezien · meldingen op deze telefoon · verbinding (woning, rol, adressen, route, live) · adressen bewerken · Cloudflare Access · ontkoppelen (bevestiging) | `meer-*` |
| **Widget** (`HomeWidget`, Glance) | twee zones + vermogen + leeftijd · verouderd na 30 min (gedimd + "Verouderd") · niet gekoppeld | niet vastgelegd (Glance rendert niet in Paparazzi) |
| **Systeemmeldingen** (`AlertNotifier`) | waarschuwing/fout als Android-melding | n.v.t. (systeem-UI) |

---

## 4. Tokens (web)

Fase 1 vervangt **alleen de waarden** onder de bestaande namen (`design/ontwerptaal/aliaslagen/my_house.css`); in
fase 2 schrijven nieuwe componenten direct de Peil-namen (`ontwerptaal.css`, Tailwind-utilities).

| Oude naam | Voor licht → Peil licht | Voor donker → Peil donker | Peil-token (fase 2) |
|---|---|---|---|
| `--bg` | `#F5F4F0` → `#EEF1F5` | `#151412` → `#0A0D12` | `background` |
| `--surface` | `#FFFFFF` → `#FFFFFF` | `#1F1E1B` → `#141920` | `card` |
| `--surface-2` | `#F0EEE8` → `#E4E8EE` | `#2A2926` → `#1E252E` | `muted` / `secondary` |
| `--sunken` | `#EAE8E2` → `#DCE3EC` | `#2A2926` → `#26303B` | `accent` |
| `--border` | `#E4E2DC` → `#AEB8C5` | `#34322E` → `#3A4451` | `border` (decoratief) |
| `--border-strong` | `#D6D3CB` → `#5B6575` | `#4A4740` → `#8E99A8` | `border-strong` |
| `--input-border` | `#807B72` → `#5B6575` | `#8A857C` → `#8E99A8` | `input` |
| `--text` | `#1C1B19` → `#0B0F14` | `#F2F0EA` → `#F2F5F8` | `foreground` |
| `--text-2`, `--muted` | `#4A4740`/`#5F5C55` → `#353E4A` | `#D6D3CB`/`#B3AFA6` → `#C2CAD5` | `muted-foreground` |
| `--primary` / `--on-primary` | `#1C1B19`/`#FFF` → `#123DA3`/`#FFF` | `#F2F0EA`/`#1C1B19` → `#8DB6FF`/`#06101F` | `primary` / `primary-foreground` |
| `--link`, `--link-hover` | `#1F5FBF`, `#174A94` → `#123DA3`, `#0C2F80` | `#8DB3F0`, `#B9CFF2` → `#9DC0FF`, `#B0CCFF` | `link`, `primary-hover` |
| `--focus` | `#1F5FBF` → `#123DA3` (+ halo `#FFFFFF`) | `#86AEEB` → `#8DB6FF` (+ halo `#0A0D12`) | `ring`, `ring-halo` |
| `--heat` / `-ink` / `-bg` / `-soft` | `#B9470F` → `#eb6834` · `#8F360B` → `#6E2405` · `#FBEDE5` → `#FDEBDD` · `#F3C9B2` → `#F4A57E` | `#F0905F` → `#d95926` · `#F7B48F` → `#FFD3B5` · `#3A2418` → `#3A1F10` · `#7A3A1A` → `#9C3D14` | `domain-heat`, `-ink`, `-bg`, `-soft` |
| `--cool` / `-ink` / `-bg` / `-soft` | `#1F5FBF` → `#2a78d6` · `#173E78` → `#0B3B73` · `#EEF3FB` → `#E0EEFB` · `#DCE6F6` → `#9ec5f4` | `#86AEEB` → `#3987e5` · `#B9CFF2` → `#C2DDFA` · `#1C2737` → `#0F2740` · `#26406A` → `#184f95` | `domain-cool`, `-ink`, `-bg`, `-soft` |
| `--boiler` | `#C26A2E` → `#00a672` | `#E0905A` → `#199e70` | `domain-boiler` (= `chart-3`) |
| `--light-on` / `--light-ink` | `#FCF1D6` → `#FDF6CF` · `#6B5200` → `#5C4300` | `#3B3115` → `#3A2E0E` · `#F0D488` → `#F7DC94` | `domain-light-on-bg` / `-ink` |
| `--rest` | `#8F8A80` → `#7D8593` | `#8A857C` → `#7A8391` | `chart-other` (+ arcering) |
| `--stale` | `#8C887F` → `#535D6C` | `#8F8B82` → `#9EA8B6` | `stale-ink` |
| `--ok` / `--ok-bg` | `#1E7A46` → `#0A4A22` · `#E6F2EA` → `#DFF2E5` | `#6CC793` → `#A6EBBF` · `#18301F` → `#0F2B1B` | `healthy-ink` / `healthy-bg` (alleen gezondheid) |
| `--warn` / `-dot` / `-bg` / `-ink` | `#9A5B00` → `#5E3300` · `#C98A1B` → `#8F4B00` · `#FFF8EA` → `#FCEFD6` · `#6B4300` → `#5E3300` | → `#FBD596` · `#F7C26A` · `#33230A` · `#FBD596` | `degraded-ink`, `degraded`, `degraded-bg` |
| `--error` / `--error-bg` | `#B42318` → `#78090F` · `#FDECEA` → `#FBE4E4` | `#F28B82` → `#FFC2C2` · `#3A1A17` → `#3A1215` | `down-ink` / `down-bg` (icoon/rand `down`) |
| nieuw (aliaslaag) | `--peil-unknown`, `-bg`, `-ink`, `--peil-stale`, `--peil-stale-hatch`, `--peil-disabled`, `--peil-ring-halo` | idem donker | `unknown*`, `stale*`, `disabled-foreground`, `ring-halo` |
| `--radius` | 18 px → 8 px | | `--radius` |
| `--font-body`, `--font-head` | Inter / Space Grotesk → systeemfont | | `font-sans` |
| `--shadow` | 1 px schaduw → 1 px rand (`rgb(11 15 20 / .10)`) | `none` | randen in plaats van schaduwen |

**Typografie (fase 2; fase 1 alleen `.q`):** body 15 → 16 px (`text-base`), meta 13 → 14 px (`text-sm`), h1 32/26 →
24 px (`text-2xl font-bold`), h2 20 → 20 px bold (`text-xl font-bold`), h3 16 → 18 px (`text-lg font-semibold`),
kaartwaarde (`.big-num`) → `text-3xl font-semibold` met proportionele cijfers; held (draaiknop) `text-5xl`. Tijden in
lijsten, assen en tabellen `cijfers` (tabular-nums). Getal en eenheid met vaste spatie (`20,6&nbsp;°C`).

---

## 5. Componenten (web)

### 5.1 Iconen: eigen SVG → lucide

| Eigen icoon (`Icons.tsx`) | Gebruik | lucide | Opmerking |
|---|---|---|---|
| `HomeIcon` | merk-teken | `house` | |
| `GridIcon` | Overzicht | `layout-grid` | |
| `ThermoIcon` | Klimaat | `thermometer` | |
| `BulbIcon` | Licht/Verlichting | `lightbulb` (aan) / `lightbulb-off` (uit) | |
| `BoltIcon` | Energie | `zap` | |
| `TrendIcon` | Optimalisatie | `trending-up` | |
| `DeviceIcon` | Apparaten | `plug` | het telefoonsymbool leek op "Telefoons" |
| `GearIcon` | Systeem | `settings` | |
| `UserIcon` | Account | `user` | |
| `HelpIcon` | Help | `life-buoy` | `circle-help` is gereserveerd voor **Onbekend** |
| `MenuIcon` | Meer | `menu` | |
| `PlusIcon` | Apparaat toevoegen, toevoegen | `plus` | |
| `FlameIcon` / `SnowIcon` | Verwarmen / Koelen | `flame` / `snowflake` | |
| `CloudOffIcon` | offline-banner | `wifi-off` | |
| `CheckIcon` | gedaan, bevestigd | `check` | nooit `circle-check` (= Gezond) voor "klaar" |
| `CopyIcon` | koppelcode kopiëren | `copy` | |
| `UploadIcon` | back-up controleren/terugzetten | `upload` | |
| — (nieuw) | status | `circle-check` · `triangle-alert` · `octagon-x` · `circle-help` · `clock-alert` · `circle-pause` · `circle-slash` · `info` | §4.3 ONTWERPTAAL |
| — (nieuw) | boiler · beleid · handmatig · readback bezig · terug naar de Hub · uitloggen | `shower-head` · `ban` · `hand` · `loader-circle` · `arrow-left` · `log-out` | |

Iconen naast tekst zijn `aria-hidden`; 20 px in knoppen en titels, 18 px in badges, 24 px in de ondernavigatie.

### 5.2 Vertaling per component

| Huidig (klasse / component) | Peil-component | Fase 1 (aliaslaag) | Fase 2 (shadcn React 18, forwardRef) | Statusvorm en microcopy |
|---|---|---|---|---|
| `.btn` · `.btn.primary` · `.btn.ghost` · `.btn.danger` · `.btn.small` | Button: secundair · primair · stil · gevaar · klein | randen ≥ 3:1, primair blauw, hover zonder opacity, uitgeschakeld met stippelrand en `--peil-disabled` (geen opacity) | `Button` (cva, `asChild` via Radix Slot) met `forwardRef<HTMLButtonElement>`; klassen `btn`/`primary`/… blijven als haak | label = werkwoord + object (blijft); bezig: `loader-circle` + "Opslaan…" |
| `.icon-btn` · `.sp-btn` (−/+) | Button `icoon` | rand ≥ 3:1 | `Button size="icoon"` (44 px; −/+ blijven 48–56 px) | `aria-label` blijft ("Doeltemperatuur hoger") |
| `.card` · `.card.tight` · `.dv-card` | Card / StatKaart (C-05) | radius 8, rand decoratief, geen schaduw | `Card` (`article`/`section` blijft), `p-4 gap-3` | elke waarde: eenheid + kwaliteit + bijgewerkt (bestaat al) |
| `.pill.heat` · `.pill.cool` · `.pill.override` · `.pill.neutral` | domeinchip (C-44) · Handmatig-label (`hand`) | heat/cool-inkt ≥ 7:1 | `Badge` variant `domein` (`domain-*-bg` + `-ink` + icoon `flame`/`snowflake`); override → `hand` + "Handmatig tot 16:00" | geen 2 px statusrand (vorm ≠ Beperkt) |
| `.pill.ok/.warn/.error/.info` · `.state-line.ok/.warn/.error` · `.dot.*` | StatusBadge (C-03) | kleuren Peil; **vorm nog niet** (stip zonder icoon) | `StatusBadge` met `circle-check`/`triangle-alert`/`octagon-x`/`circle-help` + woord; `.dot` verdwijnt achter de badge; klassen `state-line ok` blijven als haak | Gezond · Beperkt · Storing · Onbekend + reden (§10) |
| `.q.measured/.derived/.estimated/.missing` | Meetkwaliteit (C-07) | gemeten neutraal (nooit groen), ontbreekt = onbekend-stippel, 14 px | inline `· gemeten` (`text-sm font-semibold text-muted-foreground`); geschat: "≈" bij de waarde | gemeten · afgeleid · ≈ geschat · geen meting (§10) |
| tijd "2 min geleden" bij waarden | Versheid (C-06) | — | `dl` Bron/Bijgewerkt/Geprobeerd met `<time datetime>` waar ruimte is; inline op tegels | verouderd: gearceerd vak "Laatst bekend · 15:54" |
| `.note.info/.warn/.error/.ok` | melding-info / -beperkt / Foutpaneel (C-14) / neutraal | info/warn/error Peil-kleuren; `.note.ok` blijft groen (**correctie nodig**, §8.1) | `Alert` varianten met icoon `info`/`triangle-alert`/`octagon-x`; `note ok` → neutraal of info (klaar ≠ gezond) | fout: wat + waarom + wat je kunt doen + code (bestaat deels) |
| `.offline-banner` (+ `.info`) | VerbindingsBanner (C-16) | neutraal-onbekend met stippelrand (niet amber) | `role=status` blijft; `wifi-off`; `border-b-2 border-dashed border-unknown bg-unknown-bg` | tekst blijft (zie §10) |
| `.is-offline .live`, `.from-snapshot .card` (opacity) | Verouderd (T-02) | arcering + stippelrand in plaats van opacity | per kaart `waardevak-verouderd` + badge Verouderd | "Laatst bekend · 15:54" |
| `.light-tile` (`.on`, `:disabled`) | LichtTegel (C-45) | aan 8,5:1; onbereikbaar volle tekst + stippel; **rand uit = 2,0:1 (correctie nodig)** | `button[aria-pressed]` met de zichtbare tekst als begin van de toegankelijke naam (lost axe WCAG 2.5.3 op): tegel zonder `aria-label` (naam "Keuken Uit"), groepsknop "Uit" + sr-only " — Keuken aanzetten" (geen test gebruikt deze namen); aan `domain-light-on-*` + `lightbulb`; uit `border-strong` + `lightbulb-off`; onbereikbaar `unknown` + "Onbekend — niet bereikbaar" | |
| `.mode-btn` (`.heat/.cool[aria-pressed]`, `.blocked`) | KlimaatZone modusknoppen (C-44) | kleuren Peil | actief = domeinstijl + `check` (niet primair-blauw); geblokkeerd = `aria-disabled` + slot `lock` + `melding-gate` | "Koelen geblokkeerd. Een andere ruimte verwarmt; de buitenunit kan niet tegelijk koelen." (bestaande zin) |
| `.c-choices button[aria-pressed]` (ventilator, scènes) · `.segmented` | ToggleGroup | geselecteerd 2 px inktrand | `ToggleGroup` (Radix, React 18) met `aria-pressed`; **niet** als tab | |
| `Tabs.tsx` (`.tabs [role=tab]`) | Tabs (§21) | selectie alleen vlak (1,3:1): **correctie nodig** | eigen `Tabs.tsx` **blijft** (toetsenbordlogica, ids, tests); alleen stijl: < 768 omlopende knoppen met actief `bg-foreground text-background`, ≥ 768 onderstreping 4 px `primary` | |
| `.c-dial` (draaiknop) | Huis-set (C-43/44) | ring heat/cool-soft | ring `domain-*-soft`, waarde `text-5xl`, concept cursief blijft | readback: `loader-circle` "Verzonden · wacht op bevestiging" |
| `.band` (comfortband) | Meter-variant | | track `chart-meter-track`, vulling `domain-heat-soft`, markering `domain-heat` met 2 px `card`-ring | |
| `.c-status.*` (readback) | ApparaatBediening (C-45) | kleuren Peil | `role=status`; bevestigd = `check` + neutraal; mislukt/geweigerd = `octagon-x` + `down-ink`; time-out = `triangle-alert` | "Bevestigd", "Niet bevestigd — er is niets gewijzigd" (§10) |
| `.dv-protected` (boiler) | BeleidsSlot (C-13) | | `ban` + "Alleen meten — schakelen is altijd uitgeschakeld (beleid)." Geen schakelaar in de DOM (blijft zo) | |
| `.c-unlinked` | NietGekoppeld (T-12a) | | `melding-onbekend` + `link-2-off` | "Nog geen airco gekoppeld" (blijft) |
| `.field`, `input`, `select`, `textarea` | Input/Textarea; **native `<select>` blijft** | rand `input` ≥ 3:1 | shadcn `Input`/`Textarea` (forwardRef); `select` blijft native met Peil-stijl (e2e gebruikt `selectOption`; mobiel beter) | label boven veld, hulptekst via `aria-describedby` |
| `.check-row`, `.choice-card`, `fieldset.choice` | Checkbox / RadioGroup | | native inputs houden (20 px in een rij ≥ 44 px), Peil-stijl; geen Radix-radio (tests gebruiken `getByLabel`/`check()`) | |
| `.progress` | Progress | | `role=progressbar` blijft; vulling `primary`; onbepaald zonder beweging bij reduced motion | |
| `.wizard`, `.wizard-step` | Wizard / Stepper (C-36) | klaar = inkt + `check` (niet groen) | `nav[aria-label="Stappen"] > ol`, `aria-current="step"`; < 1024 horizontaal scrollend blijft (bestaande keuze) of alleen bollen | "Stap 1 van 10" blijft |
| `.sheet` (Meer) · `.sheet-backdrop` | Sheet | | **eigen focusval en Esc blijven** (tests `shell.test`); alleen stijl: `rounded-t-xl`, `bg-popover`, `shadow-lg`, overlay `bg-black/50` | |
| `table` · `table.stack-mobile` | ResponsieveLijst (C-34) | | kop `bg-muted`, `th scope`, rij `min-h-touch`; `stack-mobile` (kaarten < 640) blijft | |
| `.code`, `.code-block`, koppelcode | CodeWeergave (C-40) / KopieerVeld (C-15) | | mono, groepen van 4, `copy`, terugval zonder clipboard ("Tekst geselecteerd…") | |
| `.qr-box` | — | blijft **wit**, ook in donker | idem | QR moet zwart-op-wit blijven om te scannen |
| `details > summary` | Collapsible | | zichtbare `chevron-down` die draait (review 2.6) | |
| `.skip-link` | Skiplink | ring Peil | `knop-primair` bij focus | "Naar de inhoud" (blijft) |

---

## 6. Per scherm (web)

Alle schermen: kop = `PageHeader` (eyebrow `text-sm text-muted-foreground`, h1 `text-2xl font-bold`, acties rechts),
inhoud `max-w-content`, kaarten `gap-4`, secties `gap-6`. Hieronder alleen wat per scherm specifiek is.

| Scherm | Vertaling naar Peil |
|---|---|
| **Eerste start / Inloggen** | Kaart 420 px blijft; `h1` 24 px; foutmelding `role=alert` als Foutpaneel `veld`-niveau (`octagon-x`, `down-ink`), veldrand 2 px `down` + `aria-invalid`. Primair blauw "Account aanmaken"/"Inloggen". Merk: `house` + "Huis". |
| **Server niet bereikbaar** | Foutpaneel paginaniveau: kop "De server is niet bereikbaar", zin (blijft), knop "Opnieuw proberen" (secundair, `rotate-ccw`). Geen rood vlak voor een bron die weg is: `unknown`-stijl met `wifi-off` (bron weg ≠ gemeten falen). |
| **Woning aanmaken** | Formulier met labels boven velden; tijdzone native `select`; uitwegen als links met `chevron-right`. |
| **Schil** | Zijbalk: `bg-sidebar`, items `min-h-touch rounded-lg`, actief `bg-accent font-bold` + inset-balk 4 px `primary` + `aria-current` (NavLink zet die al). Voet: "Ingelogd als …", **ThemaKeuze** (§9.2), Account/Help, Uitloggen (`log-out`), versie. Bovenaan onder het merk: **"Terug naar de Hub"** (§9.1). Kopbalk mobiel `sticky z-nav bg-card border-b`; ondernavigatie `h-16 + safe area`, icoon 24, label **14 px** (nu 12), actief = inset-balk 3 px boven + vet. Meer-sheet: Hub-link en ThemaKeuze bovenaan de voet. |
| **Statusregel / offline** | VerbindingsBanner: offline = stippelpil/banner `unknown` + `wifi-off`; API start op = `info` + `loader-circle` (statisch bij reduced motion); live geweigerd = `info`; Cloudflare Access verlopen = `unknown` + link. Kaarten worden **verouderd** (stippelrand + badge "Verouderd" + gearceerd waardevak), niet gedimd. Bediening blijft uit met de reden (bestaande zin). |
| **Overzicht** | Volgorde blijft. "Let op"-blok wordt het paneel **"Wat actie nodig heeft"** (zelfde woorden als de Hub, Peil §1 principe 8) (rand 2 px in de slechtste status; elk item as-label + titel + uitleg + één werkwoordlink). Achtergronddienst = StatusBadge **Gezond** + "Laatste hartslag 8 s geleden" (gemeten, dus groen mag — met icoon en woord). Zonekaart = KamerKaart: titel + domeinchip (Verwarmen `flame` / Uit neutraal), waarde `text-3xl` + "°C" + `· gemeten` + "2 min geleden", −/+ rond de waarde met `aria-live` (bestaat; mag een `output` worden), comfortband als meter. Verlichting: LichtTegels. Vandaag: totaal + dunne segmentbalk + lijst als tabel (§7). Meldingen: ErnstLabel (dikke linkerbalk) per melding + "Gezien" (secundair, klein); de naam wordt `Gezien (Melding … afhandelen)`, zodat de zichtbare tekst in de naam staat (axe 2.5.3) en de tests blijven werken (e2e 07 zoekt de deeltekst "Melding … afhandelen", `offline.test` de regex `/Melding … afhandelen/`). |
| **Klimaat › Bediening** | Zonetabs en onderdeeltabs: Peil-tabs (één tabrij per niveau blijft; de bestaande geneste tablist is een bewuste structuur en blijft). Draaiknop: ring `domain-heat-soft`/`domain-cool-soft`, doel `text-5xl`. "Kies eerst een modus" = `melding-info`. Geblokkeerde modi = gate-stijl (slot) met de bestaande uitleg. Handmatig 2 uur (secundair) / Terug naar schema (primair als het van toepassing is). Sensor verouderd = Versheid verouderd + bediening uit met reden. Schema vandaag: segmentbalk verwarmen/koelen met labels **buiten** de segmenten als ze niet passen (§7). |
| **Klimaat › Weekschema** | Weekbalken als §7; dagtabs Peil; blokken als formulierrijen met veldfout per veld + foutsamenvatting `role=alert`; vaste opslaanbalk `.sched-save` boven de ondernavigatie (bestaat; krijgt `data-actiebalk` zodat de scroll-padding meegroeit, z-index `z-sticky`). "Niet opgeslagen wijzigingen" = `info`. |
| **Klimaat › Profielen / Instellingen** | Leeg-toestand met wat + waarom + knop ("Nieuw profiel"). Instellingen: formulieren Peil; opgeslagen = "Opgeslagen." `role=status` (blijft). |
| **Verlichting** | Niet-bereikbaar-melding `unknown` (niet amber). Groepskaart: titel + "3 van 3 aan · scène Lezen"; aan/uit-knop `aria-pressed` (primair = aan); helderheid-slider `accent-color: domain-light-on-ink`; scènes als ToggleGroup. Onbereikbare groep: badge Onbekend + "niet bereikbaar" (tekst blijft). Knoppen: conflict `.lt-conflict` = Beperkt-rand links (dikke linkerbalk, `triangle-alert`), simuleren = `info`. |
| **Energie** | Periodekiezer = ToggleGroup (geen tab) als hij geen tabpaneel heeft; **hier wel een tabpaneel** (`TabPanel`), dus Peil-tabs blijven. Nu: StatKaart met vermogen + `· gemeten` + tijd. Meterkaarten: StatKaart-raster. Grafiek en tabel: §7. Kosten: `dl` met cijfers. "Wat wordt gemeten": legenda met Meetkwaliteit-vormen. Exporteren/Tarieven/Meetgrenzen: formulieren Peil. |
| **Optimalisatie** | Experimentstatus als neutrale labels (Gepland, Loopt, Afgerond, Gestopt) en Gepauzeerd = `circle-pause` info. Nu is "Loopt" groen (`pill ok`) en "Gepauzeerd" amber (`pill warn`): beide fout volgens Peil (loopt ≠ gezond, pauze ≠ beperkt). Adaptief model: waarden met kwaliteit. |
| **Apparaten** | Apparaatkaart: online = neutraal "Bereikbaar" (verbonden ≠ gezond); offline = Onbekend + "niet bereikbaar sinds …"; compatibiliteit = Validatielabel-stijl (gestippelde mono-tag: "getest" / "nog niet getest" / "niet ondersteund"). Boiler: BeleidsSlot. Zigbee-aftelling = Aftellen (C-22, mm:ss, `cijfers`, aankondiging op drempels). |
| **Systeem** | Statuscontroles als SysteemStatusPaneel-rijen (naam + StatusBadge + reden); "Niet ingesteld" neutraal (open ≠ rood, regel 12). Veilige modus = `melding-beperkt` met knop. Back-ups: lijst als ResponsieveLijst; "Gelukt" = neutraal + `check`, "Mislukt" = Storing. Herstellen: Wizard-stappen 1–4. |
| **Account** | Tabs Peil. Telefoons: CodeWeergave (groepen van 4, aftelling), QR wit. Logboek: tabel met `cijfers` voor tijden. |
| **Installatiewizard** | Stepper Peil; "Klaar om te installeren." wordt **neutraal-info** (klaar ≠ gezond); controles = rijen met StatusBadge. Gele punten heten **Beperkt** maar de zin "Gele punten zijn geen blokkade" wordt "Punten met Beperkt zijn geen blokkade" (§10). |
| **Help** | Lopende tekst `max-w-prose`; "Foto volgt" = NogNietBeschikbaar-paneel (`hourglass`). |
| **Kijker** | Bediening blijft verborgen (bestaand gedrag); RolMelding (C-49) "Je kijkt als **kijker**: je kunt hier niets wijzigen." alleen als die tekst er al is — anders niet toevoegen (geen nieuwe functie). |

---

## 7. Grafieken (dataviz)

Procedure volgens de dataviz-skill: vorm eerst, dan kleur per taak, palet **gevalideerd met het script** (licht en
donker), markeringen, interactie, toegankelijkheid. Kleur volgt de entiteit, nooit de rangorde.

| Grafiek | Vorm (blijft) | Peil-kleuren | Markeringen | Tweeling / a11y |
|---|---|---|---|---|
| **Energie per uur/dag/maand** (`EnergyChart`, gestapeld airco/boiler/overig) | gestapelde staven (verandering in de tijd van een geheel) | airco `chart-1`, boiler `chart-3`, overig `chart-other` + 135°-arcering; validator PASS licht én donker | staven ≤ 24 px, 4 px afgerond aan de bovenkant, 2 px `card`-gat tussen segmenten; **onvolledig** = 45°-arcering `arcering-geschat` + stippelrand `stale` (nu amber = Beperkt, fout); **lopend** = stippellijn `chart-axis` zonder opacity; **toekomst** = haarlijn `chart-grid`; vlag (opmerking) = `info`-rondje met `!`-glyph in inkt | "Toon als tabel" bestaat en blijft (kwaliteit per rij); `role=img` + samenvatting blijft; tooltip mag, nooit enige bron |
| **Dunne energiebalk Vandaag** (`energy-bar`, 3 delen) | dunne segmentbalk naast een lijst met de cijfers (vorm "thin segmented bar") | idem | `h-2 rounded-full`, 2 px vlakkleur tussen delen, overig gearceerd; geen tekst in de balk | de lijst eronder draagt de cijfers; balk `aria-hidden` of `role=img` met samenvatting (bestaat) |
| **Laatste 24 uur** (SVG-lijn gemeten vs doel) | lijn, één y-as | gemeten `chart-1` 2 px; doel `chart-limit` (inkt) 1,5 px trap-lijn — **niet** `--heat` (doel is geen reeks "verwarmen") | gat bij ontbrekende meting (lijn breekt + "Geen meting"); verouderd na laatste punt gearceerd; eindpunt 8 px met 2 px `card`-ring + direct label | legenda bestaat (2 reeksen); tabel per uur + toetsenbord: **gebouwd** na akkoord (§18, ADR-0013). Gat = lijn breekt + "Geen meting" + arcering: gebouwd. Nog niet: "verouderd na laatste punt" en het eindpunt met direct label (§18) |
| **Schema vandaag / weekschema** (`schedule-bar`, `week-bar`) | tijdlijnbalk (segmenten in de tijd) | verwarmen `domain-heat-soft` met tekst `domain-heat-ink` (5,49:1 / 4,93:1, review 1.5), koelen `domain-cool-soft` + `domain-cool-ink`, uit = `chart-gap` + arcering | labels alleen in een segment als ze passen, anders weg (de tekst staat ook in `aria-label`); "overgenomen van gisteren" (`.carried`) = arcering in plaats van opacity 0,75 | `role=img` + volledige tekst in `aria-label` (bestaat); de blokkenlijst is de tabel |
| **Android uurstaven** (`HourBars`) en vermogensbalk | als web | `chart-1`/`chart-3`/`chart-other` uit de Compose-tokens | idem; onvolledig = arcering + stippel (nu oranje stippel) | `contentDescription` met de totalen (bestaat); de meterrijen eronder zijn de tabel |

Niet doen: dubbele y-as; status-kleuren (groen/amber/rood) als reeks; percentages in de balk; skelet bij verversen.

---

## 8. De twee fasen (web)

### 8.1 Fase 1 — aliaslaag (alleen CSS, geen Tailwind)

1. Kopieer `design/ontwerptaal/aliaslagen/my_house.css` naar `apps/web/src/peil-alias.css` (herkomstkop houden).
2. `main.tsx`: `import './styles.css'; import './peil-alias.css';` (alias **ná** styles.css). Fontsource-imports
   mogen blijven tot fase 3 (geen functie; de alias zet `--font-body` op het systeemfont).
3. `index.html`/`vite.config.ts`: `theme-color` en manifest-kleuren naar Peil (`#EEF1F5`; donker `#0A0D12` via
   `<meta name="theme-color" media="(prefers-color-scheme: dark)">`). Iconen blijven.
4. Klaar: alle kleuren, radii, randen en de focusring volgen Peil in licht en donker; `.q` 14 px en neutraal;
   onbereikbare lichttegel en offline zonder opacity; "stap klaar" niet groen. **Geen** klasse, testid of tekst verandert.

**Wat fase 1 nog niet oplost** (eerlijk): status als stip zonder icoon/woord (markup), tekst < 14 px buiten `.q`
(`.small` 277×, 33 CSS-declaraties, inline maten), groene klassen voor niet-gezondheid in de markup
(`.note.ok`, `.pill.ok` voor "Bereikbaar", `.c-status.confirmed`), opacity op enkele plekken die de aliaslaag niet
noemt, en de selectie-aanduiding van tabs/navigatie. Een deel daarvan kan zonder markupwijziging in de aliaslaag; dat
hoort in de **generator** (`bouw/aliaslagen.mjs`, versie 1.1.1), niet met de hand in de kopie. Voorstel aan de bron:

| Selector | Nu (gemeten) | Voorstel 1.1.1 |
|---|---|---|
| `.light-tile` (uit) | rand `--border` 2,0:1 | `border-color: var(--border-strong)` (5,89:1) |
| `.nav-link.active`, `.bottom-link.active` | alleen vlak (`surface` op `bg` 1,1:1) + schaduw | `box-shadow: inset 4px 0 0 var(--primary)` resp. `inset 0 3px 0 var(--primary)`; geen schaduw |
| `.tabs [aria-selected=true]`, `.segmented [aria-pressed=true]` | vlak 1,3:1 | 2 px inktrand of onderstreping `--primary` |
| `.note.ok` | groen ("Klaar om te installeren.") | `background: var(--sunken); color: var(--text)` |
| `.c-status.confirmed` | groen | `color: var(--text)` (het `check`-icoon komt in fase 2) |
| `.sp-btn:disabled`, `.c-choices button:disabled`, `.icon-btn:disabled` | opacity 0,4–0,45 | als `.btn:disabled` (stippelrand + `--peil-disabled`) |
| `.week-bar .carried`, `.lt-map.is-off`, `.en-col.is-running .en-bar` | opacity 0,7–0,75 | arcering `--peil-stale-hatch` |
| `.en-col.is-incomplete .en-bar`, `.bar.incomplete`, `.en-flag` | amber (`--warn`) | stippel `--peil-stale` + arcering; vlag `--cool-bg`/`--cool-ink` |
| `.small`, `.card-sub`, `.eyebrow`, `.bottom-link` | 12–13 px | 14 px — **alleen** na een meting op 320/360 (e2e `10-mobile`: geen zijwaartse scroll) |

### 8.2 Fase 2 — Tailwind 4 + shadcn/ui, React 18 met forwardRef

**Afhankelijkheden** (versies vastpinnen op het moment van bouwen; gemeten op 9 nov 2026: `tailwindcss` en
`@tailwindcss/vite` 4.3.5 — peer `vite ^5.2` dus Vite 5.4 blijft —, `lucide-react` 1.70.0, `class-variance-authority`
0.7.1, `tailwind-merge` 3.7.0, `clsx`, Radix-pakketten per component (`@radix-ui/react-slot` 1.5.0 e.a., peer React 18)).
Geen CDN, geen webfonts. `components.json` uit `design/ontwerptaal/`, met `rsc: false`, `tsx: true`,
`css: "src/peil/peil.css"`, aliassen `@/components` → `src/components` (Vite-alias + `tsconfig` `paths`).

**CSS-opbouw (één bestand `src/peil/peil.css`):**
```css
@layer theme, base, legacy, components, utilities;
@import "tailwindcss";
@import "./ontwerptaal.css";                 /* gepinde kopie, herkomstkop */
@import "../styles.css" layer(legacy);       /* bestaande CSS onder de utilities, boven preflight */
@import "../peil-alias.css" layer(legacy);   /* tot fase 3 */
```
Zo winnen utilities van de oude CSS, en de oude CSS van Tailwinds preflight (koppen, lijsten, knoppen houden hun
huidige reset tot ze omgezet zijn). De twee eigen kleurblokken in `styles.css` gaan weg zodra `ontwerptaal.css` erin
zit; `ontwerptaal.css` heeft **geen** `prefers-color-scheme`-terugval, dus het themascript (§9.2) is vanaf dat moment
verplicht.

**React 18-variant van shadcn:** de huidige shadcn-componenten (new-york, Tailwind 4) zijn voor React 19 geschreven
(`ref` als gewone prop, `React.ComponentProps<"button">`). In React 18 gaat een `ref` dan verloren. Elk gekopieerd
component krijgt daarom `React.forwardRef` + `displayName`, en daarna de Peil-codemod uit COMPONENTEN.md §1
(focusring, 44 px, `text-sm` i.p.v. `text-xs`, `destructive-foreground`, geen opacity-trucs, `z-overlay`/`z-toast`).
Voorbeeld:

```tsx
const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return <Comp ref={ref} data-slot="button" className={cn(buttonVariants({ variant, size }), className)} {...props} />;
  },
);
Button.displayName = 'Button';
```

**Volgorde (laag risico eerst):** (1) StatusBadge, Meetkwaliteit, Versheid, ErnstLabel, BeleidsSlot (alleen weergave);
(2) Button, Badge, Card; (3) AppSchil + ThemaKeuze + Hub-link (§9); (4) Tabs-stijl op het **bestaande** `Tabs.tsx`;
(5) Meer-sheet-stijl met de **bestaande** focusval; (6) formulieren (native `select`, `checkbox`, `radio` blijven);
(7) grafieken (§7). Per stap: afdrukken in `docs/screenshots/peil/na-fase-2/`, alle tests groen, OpenAPI byte-gelijk.

**Haakklassen blijven naast de utilities** (`className={cn('btn primary', buttonVariants(…))}`), zodat tests en de
aliaslaag blijven werken tot fase 3. **Uitgeschakeld:** Peil wil `aria-disabled` + reden. Omdat dat het gedrag
verandert (focusbaar, klik moet worden afgevangen) en 15 unit-asserties (`toBeDisabled` van jest-dom kijkt alleen
naar `disabled`) raakt, blijft in fase 2 **native `disabled`**, met de reden zichtbaar ernaast (die is er meestal al:
"Bediening is uitgeschakeld"). De overstap naar `aria-disabled` is fase 3 met aangepaste tests.

**Breekpunt:** de zijbalk komt pas vanaf `lg` (1024 px) in plaats van 821 px, zoals Peil §6. Tussen 768 en 1023 px
geldt dan de ondernavigatie. Geen test controleert 821–1023 px; de afdrukken op 768 en 1440 tonen beide schillen.

**Fase 3 (later, apart):** inline styles → utilities, `styles.css` en de aliaslaag weg, fontsource weg,
`aria-disabled`, en de Peil-testmatrix volledig (320–1440).

---

## 9. Toegestane toevoegingen

### 9.1 "Terug naar de Hub" (O-11)

- **Plaats:** in de zijbalk direct onder het merk "Huis" (≥ 1024 px) en als eerste regel van de voet van de Meer-sheet
  (< 1024 px). Niet in de kopbalk (die is op 320 px vol).
- **Vorm:** link (geen knop), `min-h-touch`, icoon `arrow-left` 20 px + tekst **"Terug naar de Hub"**; stijl
  `stil`-knop; zelfde tabblad (het is terugkeren, geen nieuw venster; dus geen "(opent in een nieuw tabblad)").
- **Adres:** uit de configuratie, **nooit** uit de URL, `document.referrer` of de gebruiker. Verborgen als het
  adres onbekend of ongeldig is — geen grijze knop, geen plaatshouder. Validatie zoals `HUB_URL` in contract 1.2 §3.2:
  `http(s)://<host>[:<poort>]`, geen pad/query/fragment/inloggegevens, geen loopback of link-local.
- **Bron (voorstel, ADR-0008 punt 3):** de optionele omgevingsvariabele **`HUB_URL`** van de Custom App (dezelfde naam
  als in het platform; de Hub geeft die mee als hij my_house via de TrueNAS-API installeert), door de webserver als
  `/_shc/config.json` (`{"hub_url": "…"}`, `no-store`, alleen na validatie) geserveerd en door de web-app één keer
  gelezen. Geen API-route en geen OpenAPI-wijziging. Leeg of afwezig = link verborgen. Dit raakt het Custom
  App-formulier (één optioneel veld) en wordt daarom als contractpunt gemeld (§13).
- **Tests:** unit (verborgen zonder adres, zichtbaar met geldig adres, verborgen bij `javascript:`/pad/loopback),
  e2e (Meer-sheet en zijbalk), axe 0.

### 9.2 Themaknop Systeem / Licht / Donker (O-09)

- **Component:** ThemaKeuze (C-50): ToggleGroup met drie tekstknoppen **Systeem · Licht · Donker**, `aria-pressed`,
  groepsnaam "Thema" (`role=group`, `aria-labelledby`), elk ≥ 44 px. Standaard **Systeem**.
- **Plaats:** zijbalkvoet en Meer-sheet-voet (onder "Ingelogd als …"). Eén plek per schil.
- **Opslag:** `localStorage` (`shc.thema` = `systeem|licht|donker`) op dit apparaat; geen serverinstelling (geen API).
- **Themascript vóór de eerste paint:** extern bestand `public/thema.js`, synchroon in `<head>` (de CSP staat alleen
  `script-src 'self'` toe, dus **geen inline script**). Het zet **altijd** het opgeloste thema: `data-theme="dark|light"`
  op `<html>` én de klasse `dark` bij donker; bij Systeem luistert het naar `matchMedia('(prefers-color-scheme: dark)')`.
  Ook `meta[name=theme-color]` volgt. Werkt in fase 1 (de aliaslaag kent `[data-theme]`) en is verplicht in fase 2.
- **Gedrag:** wisselen zonder herladen; geen animatie; focus blijft op de knop. Kijker mag het ook (alleen uiterlijk).
- **Microcopy:** "Thema" · "Systeem" · "Licht" · "Donker"; hulptekst (sr-only of klein onder de groep): "Systeem volgt
  de instelling van je apparaat."
- **Tests:** unit (opslag, opgelost thema, matchMedia-wissel), e2e (keuze blijft na herladen; donker zet `data-theme`),
  afdrukken met de knop in elke stand.

---

## 10. Microcopy (NL)

Toon volgens Peil §11: rustig, feitelijk, eerst wat er is, dan waarom, dan wat je kunt doen. **Strategie om tests
groen te houden:** het Peil-statuswoord komt in een **eigen element** (de badge) vóór de bestaande tekst; de bestaande
tekstknoop (bijv. "niet bereikbaar") blijft letterlijk staan.

| Waar | Nu | Peil | Fase | Tests die het raakt |
|---|---|---|---|---|
| `CHECK_STATE_LABEL` (statuscontroles) | In orde · Let op · Probleem · Onbekend · Niet ingesteld | **Gezond · Beperkt · Storing · Onbekend** (+ reden) · Niet ingesteld (neutraal) | 2 | geen letterlijke assertie gevonden; de klasse `state-line ok` (e2e 01) blijft |
| Overzicht/Android: kop "Let op" | Let op | **Wat actie nodig heeft** | 2 | `climate-overview.test.tsx:36` (regex `/probleem\|Let op/` voor "geen regio") → regex bijwerken. "Let op: de Hue Bridge …" (servermelding, e2e 04) is domeintekst en blijft |
| "Er is een probleem met het systeem" | zin | **Storing in het systeem** (kop) + bestaande items | 2 | `climate-overview.test.tsx:27` (regionaam) → bijwerken |
| `SEVERITY_LABEL` (meldingen) | Info · Waarschuwing · Storing | ErnstLabel: **Info · Middel · Hoog** (vaste ernstwoorden) | 2 | geen gevonden |
| Meetkwaliteit | gemeten · afgeleid · geschat · ontbreekt | · gemeten · afgeleid · ≈ geschat · **geen meting** | 2 | geen assertie op het label (de treffers "ontbreekt" zijn de servernotitie "Boiler-meting ontbreekt; overig is geschat." — domeintekst, blijft — en foutmeldingen van de testscripts); Android `quality_missing` wordt niet getest |
| `UNIT_STATUS_LABEL` | Geen airco gekoppeld · Niet bereikbaar · Bereikbaar | Niet gekoppeld (`link-2-off`) · **Onbekend** — niet bereikbaar · Bereikbaar (neutraal) | 2 | "niet bereikbaar" unit 9×, e2e 2×, Android 1× — tekst blijft, badge ervoor |
| Readback | In de wachtrij · Wordt verzonden · Verzonden, nog niet bevestigd · Bevestigd · Mislukt · Geweigerd · Vervallen | blijft; "Mislukt"/"Geweigerd" + reden; time-out: "**Niet bevestigd** — er is niets gewijzigd" waar de code dat zeker weet | — | Bevestigd/Verzonden unit 4×, Android 3× — ongewijzigd |
| Offline-banner | "Geen verbinding met de server. Je ziet de laatst bekende status (laatst bekend 15:54). Bediening is uitgeschakeld." | **blijft** (domeintekst, e2e 3×); alleen vorm en `wifi-off` | 1/2 | geen |
| Verouderd | "Sensor verouderd (182 min); externe bijsturing gepauzeerd." | blijft + badge **Verouderd** + "Laatst bekend · 12:54" boven de waarde | 2 | "Sensor verouderd" unit 3× — tekst blijft |
| Wizard | "Klaar om te installeren." (groen) · "Gele punten zijn geen blokkade" · "Er is nog iets dat aandacht nodig heeft (rood)." | neutraal · "**Punten met Beperkt** zijn geen blokkade; die los je in de volgende stappen op." · "… aandacht nodig heeft (**Storing**)." (kleur nooit als enige aanduiding in tekst) | 2 | geen letterlijke assertie gevonden |
| Laden | "Laden…" | blijft voor korte ladingen; na 10 s "…dit duurt langer dan verwacht." | 2 | geen |
| Hub-link | — | **Terug naar de Hub** | 2 (of 1) | nieuw |
| Themaknop | — | **Thema** · **Systeem** · **Licht** · **Donker** · "Systeem volgt de instelling van je apparaat." | 2 (of 1) | nieuw |

Notatie volgt Peil (`Intl` nl-NL, 24-uurs tijd, vaste spatie voor eenheden). Alle bestaande zinnen die domeinbetekenis
dragen (boilerblokkade, gedeelde buitenunit, "Kies eerst een modus", meterrollen, herstelstappen) blijven letterlijk.

---

## 11. Android: Peil in Jetpack Compose

### 11.1 Afbeelding tokens.json → Compose

Bron: `design/ontwerptaal/tokens.json` (DTCG; licht = `$value`, donker = `$extensions["nl.peil"].modes.donker`).
Fase 2 genereert daaruit `ui/theme/PeilTokens.kt` (twee `object`s `PeilLicht`/`PeilDonker` met `Color(0xFF……)`),
met een kop "Peil 1.1.0 · sha256:…", plus een JVM-test `PeilTokensTest` die elke kleur vergelijkt met `tokens.json`
(drift = test faalt). `HuisColors` blijft als naam (veel aanroepen), maar haalt zijn waarden uit Peil:

| `HuisColors` | Peil-token (licht / donker) | Opmerking |
|---|---|---|
| `ground` | `background` `#EEF1F5` / `#0A0D12` | |
| `surface` | `card` `#FFFFFF` / `#141920` | |
| `surfaceMuted` | `muted` `#E4E8EE` / `#1E252E` | |
| `ink` | `foreground` `#0B0F14` / `#F2F5F8` | |
| `muted` | `muted-foreground` `#353E4A` / `#C2CAD5` | ≥ 7:1 op card |
| `outline` | `border` `#AEB8C5` / `#3A4451` | alleen decoratief |
| `controlOutline` | `border-strong` `#5B6575` / `#8E99A8` | bedienbare randen |
| `heat`, `heatContainer`, `heatRing` (+ nieuw `heatInk`) | `domain-heat` `#eb6834`/`#d95926`, `-bg` `#FDEBDD`/`#3A1F10`, `-soft` `#F4A57E`/`#9C3D14`, `-ink` `#6E2405`/`#FFD3B5` | icoon/tekst op het vlak in `-ink` (regel 11) |
| `cool`, `coolContainer`, `onCoolContainer` | `domain-cool` `#2a78d6`/`#3987e5`, `-bg` `#E0EEFB`/`#0F2740`, `-ink` `#0B3B73`/`#C2DDFA` | **InfoBox gebruikt nu `coolContainer`** → splitsen naar `info-bg`/`info-ink` (`#E3ECFA`/`#132440`, `#0B3780`/`#C4DAFF`) |
| `ok` | `healthy` (icoon) `#0F6B33`/`#6FD394`, `healthy-ink` `#0A4A22`/`#A6EBBF` | alleen voor gemeten gezondheid |
| `warn`, `warnContainer`, `onWarnContainer` | `degraded` `#8F4B00`/`#F7C26A`, `-bg` `#FCEFD6`/`#33230A`, `-ink` `#5E3300`/`#FBD596` | |
| `error` | `down` `#B3141B`/`#FF9B9B` (icoon/rand), `down-ink` `#78090F`/`#FFC2C2`; knoppen `destructive` `#A8111A`/`#FF9B9B` | |
| `light`, `onLight` | `domain-light-on-bg` `#FDF6CF`/`#3A2E0E`, `-ink` `#5C4300`/`#F7DC94` | |
| `boiler` | `domain-boiler` `#00a672`/`#199e70` | was oranje (ΔE 2,8 van verwarmen in donker) |
| `rest` | `chart-other` `#7D8593`/`#7A8391` + arcering | |
| `dimmed` | `stale-ink` `#535D6C`/`#9EA8B6` | geen alpha meer |
| nieuw | `unknown`/`-bg`/`-ink`, `stale-hatch`, `info*`, `primary` `#123DA3`/`#8DB6FF`, `primary-foreground`, `ring`, `disabled-foreground`, `chart-1…5`, `chart-grid`, `chart-axis` | |

**MaterialTheme `colorScheme`:** `primary` = Peil `primary` (blauw; nu is dat `ink`), `onPrimary` =
`primary-foreground`, `background`/`onBackground`, `surface`/`surfaceContainer*` = `card`, `onSurface` = `foreground`,
`surfaceVariant` = `muted`, `onSurfaceVariant` = `muted-foreground`, `outline` = `border-strong`, `outlineVariant` =
`border`, `secondaryContainer` = `accent` (indicator ondernavigatie), `error`/`onError` = `destructive`/
`-foreground`, `errorContainer`/`onErrorContainer` = `down-bg`/`down-ink`. Een blauwe `primary` verandert de standaard
van alle M3-knoppen, schakelaars en voortgangsbalken — dat is de bedoeling (blauw = interactie).

**Typografie (sp, Roboto = systeemfont op Android):**

| Peil-rol | M3-stijl | Nu → Peil |
|---|---|---|
| meta 14/20 400 | `bodySmall`, `labelMedium` | 12 → **14** (ook ondernavigatie-labels en `QualityLabel`) |
| body 16/24 400 | `bodyLarge`, `bodyMedium` | 15/14 → **16** |
| knop 16 600 | `labelLarge` | 15 → 16 |
| titel 18/28 600 | `titleMedium` | 16 → 18 |
| sectie 20/28 700 | `titleLarge` | — → 20 |
| pagina 24/32 700 | `headlineSmall` (paginatitel) | 28 (`headlineMedium`) → 24 |
| waarde 30/36 600 | `displaySmall` (kaartwaarde) | 32/60 → 30; de draaiknop houdt `held` 48 met `noFontScale()` (vaste grafiek) |

**Vormen:** `Shapes(extraSmall = 4.dp, small = 6.dp, medium = 8.dp, large = 12.dp, extraLarge = 12.dp)`.
`HuisCard` radius 24 → 8 dp, `shadowElevation` 1 → 0 dp + 1 dp rand `border`; knoppen 8 dp (nu volledig rond);
statuslabel `RoundedCornerShape(50)`; risico/code 4 dp. Sheets/dialogen 12 dp.

**Aanraakdoelen: 48 dp** (Android-richtlijn, ruimer dan Peils 44 px). `Modifier.minimumInteractiveComponentSize()`
blijft aan; −/+, modus- en ventilatorknoppen, lichttegels, "Gezien", "Opnieuw proberen" en tabs ≥ 48 dp hoog;
tussenruimte ≥ 8 dp.

**Licht/donker:** volgt het systeem (`isSystemInDarkTheme()`), beide thema's uit dezelfde tokens. Een themaknop in de
app is níet besloten (O-09 geldt voor de web-app).

### 11.2 Status altijd icoon + tekst + vorm

Nieuw composable `PeilStatus(status, tekst, reden?)`: `Row` met icoon 18 dp (lucide-vorm als `ImageVector`, zie
hieronder) + woord, vorm `RoundedCornerShape(50)`, rand 2 dp (gestippeld voor Onbekend/Verouderd via `drawBehind`
met `PathEffect.dashPathEffect`), vlak per status; `semantics(mergeDescendants = true)`. Vervangt:

| Nu | Peil |
|---|---|
| `StatusDot` + tekst in de systeemregel ("Achtergronddienst actief", stip groen/oranje/rood) | `PeilStatus` Gezond/Beperkt/Storing + reden |
| `RouteChip` (groene stip "Thuis", blauw "Onderweg", oranje "Geen verbinding") | neutraal: `house` "Thuis" / `globe` "Onderweg"; geen verbinding = `wifi-off` + Onbekend-stippel (verbonden ≠ gezond) |
| `QualityLabel` (groen "gemeten", oranje "ontbreekt") | neutraal "· gemeten"; geen meting = Onbekend-vorm |
| `ZonePill` (Verwarmen/Uit/Niet bereikbaar/Handmatig) | domeinchip (`flame`/`snowflake`, `-bg` + `-ink`) · neutraal "Uit" · Onbekend "niet bereikbaar" · `hand` "Handmatig" |
| `AttentionCard` ("Let op", oranje rand) | paneel in de slechtste status (2 dp rand) + items met icoon |
| `CommandStatusText` (Tone OK groen / WARN) | `loader-circle` wacht · `check` bevestigd (neutraal) · `octagon-x` mislukt/geweigerd · `triangle-alert` time-out |
| `ConnectionBanner` (amber vlak) | `wifi-off` + `unknown`-vlak met stippelrand; "Opnieuw proberen" blijft |
| offline: alles `alpha(0.55f)` | geen alpha: kaartrand gestippeld + "Verouderd · laatst bekend 15:47" + waarde in `stale-ink` |
| widget: gedimd + "Verouderd" | `clock-alert` + "Verouderd · laatst bekend …" in `ColorProvider(day, night)` uit Peil; geen dimmen |

**Iconen:** de app gebruikt nu Material Icons (`Icons.Outlined.*`). Voor één beeldtaal met web: de ± 30 gebruikte
lucide-iconen als vector drawables (`res/drawable/lucide_*.xml`, ISC-licentie meeleveren), géén nieuwe bibliotheek.
Ondernavigatie: Start `house`, Klimaat `thermometer`, Energie `zap`, Meer `menu`.

### 11.3 Schermen

| Scherm | Vertaling |
|---|---|
| Start | kop "Thuis" + route neutraal; systeemregel = `PeilStatus`; "Let op" → paneel; zonekaart = KamerKaart; lichttegels als web (aan `domain-light-on-*`, uit `border-strong`, onbereikbaar stippel); vermogenskaart met `chart-*`; meldingenbel: teller neutraal-inkt (nooit rood) met tekst in `contentDescription` |
| Klimaat | zonetabs Peil (onderstreping 4 dp `primary`); draaiknop ring `domain-*-soft`; −/+ 56 dp; modusknoppen: actief domeinstijl + `check`, geblokkeerd slot + uitleg (bestaande zinnen); "Kies eerst een modus" = info |
| Energie | uurstaven §7; meterrijen met swatch + kwaliteit neutraal |
| Meer | lijsten met `HorizontalDivider` in `border`; "Ontkoppelen" = `destructive` met de bestaande bevestiging; formuliervelden rand `input` |
| Koppelen / Niet meer gekoppeld | Foutpaneel-stijl voor "Dit is niet jouw server" (`octagon-x`, want gemeten afwijking) en Onbekend-stijl voor "camera niet beschikbaar" |

---

## 12. Welke tests geraakt worden

**Blijft gelijk (moet groen blijven, elke fase):**
- **OpenAPI byte-gelijk.** Controle (9 nov 2026, HEAD `ef1389d`): `app.openapi()` geserialiseerd met
  `json.dumps(indent=2, ensure_ascii=False, sort_keys=True)` zonder slotregel is byte-gelijk aan
  `docs/openapi/my_house.openapi.json`. Herhaal na elke fase.
- **Haakklassen** die tests gebruiken: `.shell` + `is-offline`/`from-snapshot`, `.state-line` + `ok/warn/error`,
  `.note.error`, `.error-text`, `.note.warn`, `.c-dial`, `.big-num`, `.lt-conflict`, `.en-col` + `is-incomplete/
  is-complete/is-future/is-running`, `.en-seg-rest`, `.bar` + `incomplete`, `.en-flag`, `dl.facts`, `.q`,
  `#adressen`. Alle `data-testid`'s (o.a. `check-*`, `device-*`, `group-*`, `meter-*`, `live-power`, `pair-code`,
  `zigbee-countdown`, `export-link`). Rollen en toegankelijke namen (tablists "Zone", "Onderdelen van …",
  "Accountonderdelen"; navigatie "Hoofdmenu"/"Hoofdmenu (mobiel)"; regio's "Bediening", "Schema vandaag", …).
- **Vitest** (23 testbestanden; nulmeting 9 nov 2026: `npx vitest run` 23/23 bestanden, 164/164 tests, `tsc --noEmit` schoon): geen klasse verandert in fase 1; in fase 2 alleen de tekstwijzigingen
  van §10 (twee regionamen in `climate-overview.test.tsx`) en geen enkele `toBeDisabled` (15×), omdat `disabled` blijft.
- **E2E** (`e2e/01…10`, Playwright tegen de echte stack): `10-mobile` (44 × 44 px, geen zijwaartse scroll op 320/360)
  is het gevoeligst voor 14 px-tekst en het systeemfont — op deze machine wint "Noto Sans" in de lettertypelijst
  (bevinding MediaGuard: 3 px overloop op 390). Meten met CI-lettertypen én met Noto. `08-offline` (banner, `.shell
  is-offline`, `toBeDisabled` 11×) blijft geldig.
- **Android:** `core` 82 en `app` 36 unit-tests (BEVINDINGEN, 9 nov 2026) zijn logica, geen weergave. De teksten die
  ze via `StringsXmlTexts` controleren ("Achtergronddienst …" 11×, "Verzonden" 2×, "Bevestigd", "Niet bereikbaar")
  blijven volgens §10; `quality_missing` wordt niet getest. Geraakt in fase 2: `PresentationTest`, als `Tone`
  (MUTED/OK/WARN) een Peil-status wordt.

**Nieuw toe te voegen (fase 2):**
- axe-core in de e2e (tags `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa`) per scherm × licht/donker × 390/1440 = 0;
  ondergrens = de nulmeting in §2.2.
- Peil-matrix: 320/360/390/768/1024/1440 × licht/donker: geen overloop, geen doel < 44 px, geen tekst < 14 px,
  focusring zichtbaar en niet bedekt (Tab-doorloop op 390 en 1440).
- ThemaKeuze en Hub-link (§9).
- Drift: sha256 van `design/ontwerptaal/` en van de kopieën in `apps/web/src/peil/` (zoals `HERKOMST.md`).
- Android: Paparazzi als **test-afhankelijkheid in de repo** (zoals MediaGuard), met de afdrukset van
  `voor/android/` als vaste namen, plus `PeilTokensTest`. Dat is een buildwijziging (alleen test) en hoort bij fase 2.
- Afdrukken `docs/screenshots/peil/na-fase-1/` en `na-fase-2/` met dezelfde namen als `voor/`.

---

## 13. Risico's, open punten en contractpunten

| # | Punt | Soort | Voorstel |
|---|---|---|---|
| 1 | Bron van het Hub-adres voor "Terug naar de Hub". my_house kent het Hub-adres niet; een API-instelling zou de OpenAPI veranderen. | contractpunt | optionele env `HUB_URL` (zelfde regels als contract 1.2 §3.2) → `/_shc/config.json`; verborgen zonder geldig adres (ADR-0008). Vraagt bevestiging omdat het Custom App-formulier een optioneel veld krijgt. |
| 2 | my_house heeft de contractkopie 1.1; `HUB_URL`-regels en §13 (ontwerptaal) staan in 1.2. | contractpunt | kopie bijwerken naar 1.2 in een aparte stap (niet in deze ronde). |
| 3 | Peil §11.2 schrijft "Verstuurd — wacht op bevestiging van my_house"; my_house (web, Android, tests) zegt "Verzonden". Twee woorden voor één begrip. | ontwerptaal | my_house is eigenaar van de readback: Peil 1.1.1 neemt "Verzonden" over (één woord platformbreed). |
| 4 | Aanvullingen op de aliaslaag (§8.1 tabel) horen in de generator, niet in de kopie. | ontwerptaal | aliaslaag 1.1.1 genereren en opnieuw meten. |
| 5 | Offline toont een nooit geladen pagina eindeloos "Laden…" (afdruk `verlichting-offline-nooit-geladen-*`). | gedrag (geen uiterlijk) | aparte codewijziging: na de offline-drempel "Onbekend — nog niet opgehaald; je bent offline." |
| 6 | Tabelweergave bij "Laatste 24 uur" ontbreekt (WCAG-tweeling, Peil §10). | toevoeging | ~~vraagt akkoord van de gebruiker (besluit geldt alleen MediaGuard/MSO)~~ **opgelost:** akkoord op 10 november 2026 (tabel + toetsenbord), gebouwd op 11 november 2026 (§18, ADR-0013). |
| 7 | 14 px-tekst + systeemfont op 320 px: "Overzicht" in de ondernavigatie (5 kolommen van 64 px). | risico | meten in fase 2; terugval: icoonmarge kleiner, nooit < 14 px. |
| 8 | Tailwind 4 vraagt moderne browsers (Safari 16.4+, Chrome 111+, Firefox 128+). Oude tablets/wandpanelen kunnen breken. | risico | vóór fase 2 de gebruikte apparaten nagaan (onbekend); fase 1 heeft dit risico niet. |
| 9 | Android-afdrukken zonder echt toestel; widget niet vastgelegd; echte schermlezer (NVDA/TalkBack) en fel-zonlicht niet getest. | validatie | NIET GEVALIDEERD (hardware); hoort bij de acceptatie (08_UI). |
| 10 | Lucide op Android als vector drawables (± 30 bestanden). | keuze | ADR-0008 punt 6; alternatief Material Symbols afgewezen (twee beeldtalen). |

---

## 14. Uitvoering web (stap 3)

**Gebouwd op 9 november 2026** (branch `ccr-c133d7a0-kuz1cv`); besluiten in
[ADR-0009](../adr/ADR-0009-peil-fase-2-tailwind-shadcn-react18.md). Afdrukken ná de omzetting:
[`docs/screenshots/peil/na/`](../screenshots/peil/README.md).

| Onderdeel | Uitgevoerd | Afwijking van dit ontwerp (en waarom) |
|---|---|---|
| Fase 1 | `src/peil-alias.css` (kopie met herkomstkop, sha256 bewaakt door `src/test/peil-herkomst.test.ts`), theme-color en manifest | geen; de proefafdrukken van fase 1 zijn alleen bekeken, niet als `na-fase-1/` vastgelegd (de eindstand staat in `na/`) |
| Fase 2 basis | Tailwind 4.3.5 (`@tailwindcss/vite`), `src/peil/ontwerptaal.css` (kopie), lagen in `src/peil/peil.css`, lucide-react 1.70.0, cva, tailwind-merge, clsx, `@radix-ui/react-slot`; `components.json` (`rsc: false`) | **geen preflight** (ADR-0009 punt 2); fontsource al nu verwijderd (werd niet meer gebruikt) |
| Componenten | Button/Badge/Card (React 18, forwardRef); StatusBadge, ErnstLabel, BeleidsSlot; recepten bij de haakklassen in `src/peil/componenten.css` | de ± 150 bestaande `.btn`-plekken zijn niet herschreven naar `<Button>`: het recept zit bij de haakklasse, `buttonVariants` zet dezelfde klassen (ADR-0009 punt 3) |
| Status en kleur | §5.2 en §10 toegepast; "Online", "Gekoppeld", "Gelukt", "Loopt", "Klaar" neutraal; onbereikbaar = Onbekend; compatibiliteit als validatielabel; teruglevering `chart-5` | niet-bereikbaar-melding op Verlichting kreeg de nieuwe variant `.note.unknown` |
| Toegankelijkheid | LichtTegel zonder `aria-label` (naam "Keuken Uit"); "Gezien" en de groepsknop met een `aria-label` dat met de zichtbare tekst begint | §5.2 noemde sr-only tekst; die gaf in de e2e een dubbele treffer op de meldingstitel, dus `aria-label` (zelfde naam, WCAG 2.5.3 opgelost) |
| Toevoegingen | ThemaKeuze (§9.2) en "Terug naar de Hub" (§9.1) in zijbalk en Meer-sheet | `HUB_URL` wordt bij de start van de webcontainer gecontroleerd (`apps/web/docker/15-shc-config.sh`), niet via envsubst (ADR-0009 punt 9) |
| Tests | vitest 26 bestanden / 179 tests (o.a. `peil.test.tsx`, `peil-toevoegingen.test.tsx`, `peil-herkomst.test.ts`), e2e 23 tests waarvan `11-peil` (axe, matrix 320–1440, elementen buiten hun kaart, thema, Hub-link), infra-unittest voor het startscript, rooktestcontrole `/_shc/config.json` | Tab-doorloop met bedekte focus, forced colors en 200 %-zoom zijn niet geautomatiseerd (open) |

**Meting ná de omzetting** (zelfde 52 metingen als §2.2, `docs/screenshots/peil/na/web/axe-na.json`): axe-core 0
overtredingen (voor: `label-content-name-mismatch` op Overzicht en Verlichting, 8 metingen), tekst < 14 px 0 (voor ± 2 770
tekstknopen), bedienbare doelen < 44 px 0 (voor 58), elementen met opacity en tekst 0 (voor 108), zijwaartse scroll 0
op alle 230 afdrukken. De e2e-matrix draait daarnaast op 320–1440 px met de lettertypen van deze machine (Noto Sans)
én met DejaVu Sans (zoals de CI); op 320 px vond die 5 px overloop bij de experimentvarianten (opgelost).

**Niet gedaan in deze stap (eerlijk):** de Android-app (§11), fase 3 (inline styles, `styles.css` en aliaslaag weg,
preflight, `aria-disabled`), de tabelweergave bij "Laatste 24 uur" (§13 punt 6, toen niet besloten; later gebouwd, §18), het gedrag "nooit
geladen pagina offline" (§13 punt 5), een echte schermlezer en echte apparaten (**NIET GEVALIDEERD (hardware)**) en het
`HUB_URL`-veld in een Custom App-formulier (komt met de alles-in-één image, ADR-0004).

---

## 15. Uitvoering Android (stap 4)

**Gebouwd op 9 november 2026** (branch `ccr-c133d7a0-kuz1cv`); besluiten in
[ADR-0010](../adr/ADR-0010-peil-android-compose.md). Afdrukken ná de omzetting:
[`docs/screenshots/peil/android/`](../screenshots/peil/README.md) (zelfde namen als `voor/android/`, plus
`start-storing-*` en `peil-onderdelen*`).

| Onderdeel (§11) | Uitgevoerd | Afwijking van dit ontwerp (en waarom) |
|---|---|---|
| Tokens (§11.1) | `tools/peil_tokens.py` → `ui/theme/PeilTokens.kt` (alle kleurtokens licht/donker, typografierollen, radius; sha256 van `tokens.json` in de kop); `HuisColors` haalt elke waarde uit Peil; `PeilTokensTest` (drift) en CI-stap `peil_tokens.py --check` | ook de overige Material-rollen (secondary, tertiary, inverse, surfaceTint) uit Peil, anders verschijnt het standaardpaars |
| Typografie en vormen | rollen meta 14 · body 16 · titel 18 · sectie 20 · pagina 24 · waarde 30 · held 48 (alleen de draaiknop zonder lettergrootteschaal); radius 8 dp, risico 4, dialoog 12; kaarten met 1 dp rand zonder schaduw | de kaartwaarde (30 sp) schaalt nu wél mee met de lettergrootte (was 48 sp vast) |
| Status (§11.2) | `PeilStatus`/`StatusBadge`: icoon + vast woord + vorm, gestippeld voor Onbekend/Verouderd/Niet gekoppeld; systeemregel, "Wat actie nodig heeft", zonechip, lichttegel, route, verbindingsbanner, opdrachtregel, meldingen (ErnstLabel), meetkwaliteit en widget omgezet; geen `alpha` meer | de rand van een gekozen modus en een brandende lichtgroep in de domein-*inkt* (contrast, ADR-0010 punt 5); het vinkje boven het woord |
| Iconen | 32 lucide-iconen als `res/drawable/lucide_*.xml` via `tools/lucide_naar_vector.mjs` (lucide-react 1.70.0, zelfde als web), licentie in `assets/licenties/`; `material-icons-extended` verwijderd | — |
| Aanraakdoelen | `PeilKnop` ≥ 48 dp, −/+ 48/56 dp, modus/ventilator 52 dp, tabs 48 dp, belknop 48 dp | — |
| Schermen (§11.3) | Start, Klimaat (tabs met 4 dp-onderstreping in `primary`), Energie (uurstaven `chart-1/3/other`, onvolledig = arcering + stippel), Meer (ErnstLabel, `destructive` voor Ontkoppelen), Koppelen, Niet meer gekoppeld | het koppelscherm volgt nu het thema (was altijd donker); het zoekkader over het camerabeeld blijft wit |
| Tests (§12) | Paparazzi 1.3.5 in de repo (alleen test): 14 afdrukken × licht/donker × 360/393 dp = 56 referentiebeelden, CI `:app:verifyPaparazziDebug`; `PeilUiTest` (8), `PeilContrastTest` (5), `PeilTokensTest` (4); bestaande tests core 82 + app 36 ongewijzigd groen | — |

**Niet gedaan (eerlijk):** een rooktest op een emulator of echte telefoon (geen KVM-toegang voor deze gebruiker op de
testmachine; **NIET GEVALIDEERD (hardware)**), TalkBack, de widget in een afdruk (Glance rendert niet in Paparazzi) en
een automatische meting van aanraakdoelen in de gerenderde schermen (de maten staan als constanten in de code). Getallen
en eenheden houden de bestaande notatie (`20,6°` zonder vaste spatie): dat is opmaak in `:core`, die getest wordt en
buiten deze stap blijft.

---

## 16. Onafhankelijke review (stap 5)

De omgezette web- en Android-app zijn op 9 november 2026 onafhankelijk doorgemeten en bekeken; uitkomst, metingen en
open punten in [`PEIL_REVIEW.md`](PEIL_REVIEW.md), besluit in
[ADR-0011](../adr/ADR-0011-peil-review-verouderd-en-nooit-opgehaald.md). Daarmee is §13 punt 5 (nooit geladen pagina
offline) opgelost: "Onbekend — nog niet opgehaald" met de reden en een paginakop. §13 punten 3 en 6 (readback-woord,
tabel bij "Laatste 24 uur") bleven toen open (punt 3 is opgelost in §17, punt 6 in §18); de grafiek kreeg wel
as-labels.

## 17. Peil 1.1.1 (10 november 2026)

De kopie in `design/ontwerptaal/` is Peil 1.1.1 uit ai-server-manager (bron-commit `4ff4b3f`), met pin en driftcontrole
volgens E08 (`tools/peil_kopie.py`, `design/ontwerptaal/HERKOMST.json`, ADR-0012). Wat het voor my_house betekent:

| Groep | In my_house |
|---|---|
| E13 "Verzonden" | stond er al (`commands.ts`, `i18n.ts`) |
| E14 aliaslaag en tokens | `-soft`-banden lichter (licht) en donkerder (donker): `-ink` erop ≥ 7:1 (was 4,9–6,2:1); de correcties van §8.1 staan nu in de aliaslaag zelf; de rand van een gekozen modus en een brandende lichttegel is `-ink` (`src/peil/componenten.css`; Android deed dat al) |
| E15 offline/verouderd | stond er al (ADR-0011) |
| E16 ondernavigatie | items op labelbreedte (`flex-auto`, minimaal 44 px), actief zonder extra gewicht, `aria-current` ook op "Meer" (web); Android: actief zonder extra gewicht |
| E12 reflow met het lettertype van de CI-runner | de browsertests draaien in CI op de runner zelf (Ubuntu, zijn eigen systeemlettertype) |
| E01–E10 statuspagina | n.v.t.: my_house heeft geen platform-statuspagina (contract 1.3 §13) |

§13 punt 6 (tabel bij "Laatste 24 uur") bleef in deze stap open: daarvoor was een besluit van de gebruiker nodig
(ONTWERPTAAL §15 punt 8). Dat besluit is er; zie §18. Review: `PEIL_REVIEW.md` §11.

## 18. Tabel en toetsenbord bij "Laatste 24 uur" (11 november 2026)

Besluit van de gebruiker (10 november 2026, inventaris C09): my_house krijgt een **tabelweergave en bediening met het
toetsenbord** bij de grafiek "Laatste 24 uur" (Klimaat › Bediening). Alleen uiterlijk en toegankelijkheid: geen nieuwe
API, geen nieuw datamodel, geen nieuwe instelling. Besluiten en afwegingen in
[ADR-0013](../adr/ADR-0013-grafiek-24-uur-tabel-en-toetsenbord.md).

| Peil 1.1.1 §10 | In my_house |
|---|---|
| Tabel als tweeling van de grafiek | uitklapblok **"Toon als tabel, per uur"** onder de grafiek (dicht bij openen): per klokuur het gemiddelde, de laagste en hoogste meting en het doel; `caption`, `th scope`, tabelcijfers; onder 641 px wordt elke rij een kaart (`stack-mobile`) |
| Plotvak met het toetsenbord | één Tab-stop (`role="img"`, `tabindex="0"`, Peil-focusring); **→** eerste uur, **←** laatste uur, daarna per uur; **Home/End**; **Esc** stopt; Tab en Enter gaan gewoon door (geen focusval) |
| Voorlezen | één zichtbare leesregel onder de grafiek die ook `aria-live="polite"` is: "10 nov 12:00–13:00: gemeten gemiddeld 20,8° (laagst 20,5°, hoogst 21,0°); doel 21,0°"; de uitleg van de toetsen is de beschrijving van het plotvak (`aria-describedby`) en zichtbaar zolang het plotvak de focus heeft en er nog niets gekozen is |
| Aanwijzer en aanraken | muis en tik kiezen hetzelfde uur als het toetsenbord (band met inktranden over de volle hoogte, geen zwevende tooltip). Muis weg = keuze weg; na een tik blijft de keuze staan tot een tik ernaast. Met een muis is de plaats van de leesregel vooraf vrijgehouden, zodat de tabelknop niet verspringt |
| Eerlijkheid (gat nooit 0) | een uur zonder meting heet overal **"Geen meting"**; de lijn breekt op die uren en bij een meting zonder waarde; een stuk zonder enige meting is gearceerd (`arcering-gat`) met het woord erin als het past (anders weg, nooit half) en de uitleg "Gearceerd: geen meting (nooit 0)."; een losse meting is een stip van 8 px met een ring van 2 px in de kaartkleur; een meting zonder waarde telt niet meer als 0 in de schaal |

**Gemeten (11 november 2026, Chromium, echte API met metingen zoals de worker ze opslaat):** axe-core 0 overtredingen
(wcag2a/aa, 21a/aa, 22aa) op 320 · 360 · 390 · 768 · 1024 · 1440 px, licht en donker, met open tabel en een gekozen
uur; geen horizontale scroll; geen tekst onder 14 px; geen doel kleiner dan 44 px; focusring ≥ 3:1; de tabelknop
verschuift niet als de muis over de uren gaat; een tik op een nagebootst aanraakscherm (390 px) kiest een uur en de
keuze blijft staan. Tests: `apps/web/src/test/history-chart.test.tsx` en `apps/web/e2e/12-grafiek-24-uur.e2e.ts`.
Afdrukken: `docs/screenshots/peil/review/12-grafiek-24-uur-*.png`.

**Niet gebouwd (eerlijk):** de Peil-markering "verouderd na het laatste punt" en het eindpunt met direct label (§7): de
x-as loopt tot het laatste punt, dus er is geen vlak na het laatste punt. **Niet getest:** een echte schermlezer (NVDA,
TalkBack, VoiceOver) en echte telefoons of tablets — **NIET GEVALIDEERD (hardware)**. In de leesmodus van een
schermlezer gaan de pijltjes naar de schermlezer zelf; daar is de tabel de weg (ADR-0013 punt 3). De Android-app heeft
deze grafiek niet; daar verandert niets.

**Bijgewerkt na de onafhankelijke ontwerpreview (11 november 2026):** de twee punten onder "Niet gebouwd" zijn er nu
wel. Noemt de server de sensor verouderd, dan stopt de lijn bij de laatste meting en volgt een gearceerd stuk met
stippelrand tot nu, met "Verouderd sinds …" als het past en altijd de pil "Verouderd" met de laatst bekende waarde
eronder. De laatste meting is een eindpunt van 12 px (vanaf 34 rem met de waarde ernaast, bij een verse sensor). Verder:
`<figure>` met bron en "bijgewerkt" in het bijschrift, hele uren op de tijdas vanaf 34 rem, de aslabels binnen het
plotvak en de legenda alleen bij een grafiek. Besluit en afwegingen: ADR-0014; bevindingen en metingen:
[`PEIL_REVIEW.md`](PEIL_REVIEW.md) §12. "NIET GEVALIDEERD (hardware)" hierboven geldt onveranderd.
