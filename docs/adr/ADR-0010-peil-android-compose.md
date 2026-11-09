# ADR-0010 — Android-app naar Peil: gegenereerde tokens, lucide als vector drawables en Paparazzi in de repo

- **Status:** aangenomen (2026-11-09, stap 4: Android-app naar Peil)
- **Context-ID's:** geen eigen Excel-ID (uiterlijk); ADR-0008 punten 5 en 6; `docs/ontwerp/PEIL_MIGRATIE.md` §11, §12 en
  [§15](../ontwerp/PEIL_MIGRATIE.md#15-uitvoering-android-stap-4); Peil 1.1.0 (`design/ontwerptaal/`); besluit van de
  gebruiker "ook de Android-apps van MediaGuard en my_house in Peil-stijl".

## Context

De native app (`apps/android`, Kotlin + Jetpack Compose) had een eigen "Rustig huis"-thema: warme ondergrond, randen
van 1,3:1, groen voor "gemeten" en "Thuis", status als gekleurde stip, alles gedimd (alpha 0,55) zonder verbinding,
boiler in bijna dezelfde oranje als verwarmen en tekst van 11–13 sp. Alleen het uiterlijk mag veranderen: functies,
API-aanroepen, datamodel, rechten en de installatie blijven. De app had geen schermafdruktests; de nulmeting
(`docs/screenshots/peil/voor/android/`) is in een kopie buiten de repo met Paparazzi gemaakt.

## Besluit

1. **Kleuren en typografie uit `tokens.json`.** `tools/peil_tokens.py` genereert `ui/theme/PeilTokens.kt` (alle
   kleurtokens licht en donker, de typografierollen, de radius) met de sha256 van de gepinde `tokens.json` in de kop.
   `PeilTokensTest` vergelijkt elke waarde met `tokens.json` en de sha256; CI draait daarnaast `peil_tokens.py --check`.
   `HuisColors` blijft als naam (veel aanroepen) maar haalt elke waarde uit Peil; nieuwe code leest `Huis.peil`.
   Alle Material 3-rollen (ook secondary, tertiary, inverse, surfaceTint) komen uit Peil, zodat nergens het
   standaardpaars van Material verschijnt. Typografie: niets onder 14 sp, body 16, paginatitel 24 vet, kaartwaarde 30,
   draaiknop 48 ("held", blijft zonder lettergrootteschaal omdat de ring een vaste maat heeft).
2. **Iconen: lucide als vector drawables, geen bibliotheek.** `tools/lucide_naar_vector.mjs` zet 32 lucide-iconen uit
   `lucide-react` 1.70.0 (dezelfde versie als de web-app; het script weigert een andere) om naar
   `res/drawable/lucide_*.xml`, met padgegevens opnieuw geschreven (expliciete scheidingstekens, ook tussen
   boogvlaggen). De licentie (ISC, deels MIT via Feather) gaat als `assets/licenties/lucide-LICENSE.txt` mee in de APK.
   `material-icons-extended` is uit de afhankelijkheden: geen twee beeldtalen (ADR-0008 punt 6).
3. **Status altijd icoon + vast woord + vorm** (`ui/PeilUi.kt`, zonder Compose en dus unit-getest; tekenen in
   `ui/components/Peil.kt`): Gezond · Beperkt · Storing · Onbekend · Verouderd · Gepauzeerd · Niet gekoppeld, met een
   2 dp-rand die gestippeld is voor wat niet gemeten is. Ernst van meldingen als ErnstLabel (Info · Middel · Hoog). Groen
   alleen nog voor gemeten gezondheid; route "Thuis", "gemeten", "Opgeslagen", "Bevestigd" en meldingen aan zijn
   neutraal. Zonder verbinding wordt niets meer gedimd: kaarten krijgen een gestippelde rand en
   "Verouderd · Laatst bekend · 15:47", waarden staan in `stale-ink`.
4. **Aanraakdoelen 48 dp** (Android-richtlijn, ruimer dan Peils 44 px): `PeilKnop` (primair, secundair, stil, gevaar)
   is minstens 48 dp hoog met hoeken van 8 dp; −/+ 48 dp op Start en 56 dp op Klimaat; modus- en ventilatorknoppen
   52 dp; tabs 48 dp. Uitgeschakeld = gestippelde rand op het `muted`-vlak met `disabled-foreground`, nooit
   doorzichtigheid (Material zet anders 38 % alpha op de tekst).
5. **Twee afwijkingen van het ontwerp, om contrast:** de rand van een gekozen modus (verwarmen/koelen) en van een
   brandende lichtgroep is de domein-*inkt*, niet de domeinkleur: `domain-light-on` haalt op zijn eigen vlak maar 2,8:1
   en `domain-heat` 2,8:1 (licht); de inkt haalt 8–9:1. `PeilContrastTest` bewaakt alle paren die de schermen
   tekenen (tekst ≥ 4,5:1, gedempte tekst ≥ 7:1, iconen/randen/grafiekmarkeringen ≥ 3:1) in licht en donker.
   Het vinkje van een gekozen knop staat bóven het woord, zodat "Verwarmen" op 360 dp niet midden in het woord breekt.
6. **Het koppelscherm volgt het thema.** Het was altijd donker met eigen kleurwaarden; nu Peil licht/donker zoals de
   rest. Alleen het zoekkader over het camerabeeld blijft wit in beide thema's (een donker kader verdwijnt op een
   donker beeld). Geen camerabeeld = Onbekend-vorm (`camera-off`, gestippeld); een geweigerde code of een ander
   apparaat op het adres = Foutpaneel (`octagon-x`, gemeten afwijking).
7. **Schermafdrukken als test in de repo.** Paparazzi 1.3.5 en Mockito 5.14.2 als *test*afhankelijkheden (niets
   ervan komt in de APK). `PeilAfdrukken` tekent de echte schermen en de echte schil (`HuisSchil`, daarvoor uit
   `HuisRoot` gehaald) met fictieve API-gegevens, in licht/donker × 360/393 dp: 14 afdrukken per combinatie, samen 56
   referentiebeelden in `app/src/test/snapshots/images/`. CI draait `:app:verifyPaparazziDebug` (met alle andere
   unit-tests) in plaats van `:app:testDebugUnitTest`; tolerantie 0,5 % voor anti-aliasverschillen tussen JDK-builds.
   De lange "-volledig"-versies voor de documentatie ontstaan alleen met `PEIL_VOLLEDIG=1` en horen niet bij de
   referentieset.
8. **Vaste klok voor de afdrukken.** De schermen lezen "nu" uit `LocalHuisKlok` (in de app de systeemklok, in de
   test 9 nov 2026 16:06:30 Amsterdam): relatieve tijden, begroeting en het geldende schemablok. Zelfde gedrag in de app.
9. **Widget:** Peil-kleuren via `ColorProvider(day, night)`, letters minstens 14 sp, verouderd = `clock-alert` +
   "Verouderd · Bijgewerkt …" in `stale-ink` (geen grijs dimmen meer). Glance rendert niet in Paparazzi: de widget is
   niet afgedrukt.

## Afgewezen alternatieven

- **Kleuren met de hand overnemen in `Theme.kt`.** Drift met de bron is dan onzichtbaar; de generator + test maakt een
  nieuwe Peil-versie één commando.
- **Material Symbols of `material-icons-extended` houden.** Twee beeldtalen voor hetzelfde begrip (Peil §1 principe 8).
- **Een lucide-bibliotheek voor Compose.** Een extra afhankelijkheid voor 32 iconen; vector drawables werken ook in de
  widget (Glance `ImageProvider`).
- **Afdrukken alleen in een kopie buiten de repo (zoals de nulmeting).** Dan bewaakt niets het uiterlijk; ADR-0008
  noemde Paparazzi in de repo al als stap van fase 2.
- **Rooktest op een emulator in plaats van Paparazzi.** Hier niet mogelijk (zie Gevolgen) en in CI traag en wisselvallig;
  Paparazzi is deterministisch en draait op elke JVM.

## Gevolgen

- Uiterlijk wijzigen = afdrukken opnieuw vastleggen (`./gradlew :app:recordPaparazziDebug`) en de verschillen in de
  review bekijken. De referentiebeelden kosten ± 4,3 MB in de repo.
- `quality_missing` heet nu "geen meting", "Let op" heet "Wat actie nodig heeft", een systeemfout "Storing in het
  systeem" (zelfde woorden als de web-app). Teksten met domeinbetekenis zijn gelijk gebleven; de bestaande 118
  unit-tests (core 82, app 36) zijn ongewijzigd groen.
- **NIET GEVALIDEERD (hardware):** geen echte telefoon, geen emulator, geen TalkBack. Een rooktest op een headless
  emulator was hier niet mogelijk: `/dev/kvm` is alleen voor root en de groep `kvm`, en deze gebruiker zit niet in die
  groep; zonder KVM is een emulator met API 35 op deze gedeelde machine (14,5 GiB) niet bruikbaar. Herstel buiten deze
  repo: de gebruiker in de groep `kvm` (eenmalig, door de beheerder van de machine) en dan een rooktest met
  `uiautomator dump` op aanraakdoelen ≥ 48 dp.
