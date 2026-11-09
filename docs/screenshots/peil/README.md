# Schermafdrukken voor de Peil-omzetting

Nulmeting van het uiterlijk van my_house **vóór** de omzetting naar de ontwerptaal Peil 1.1.0 (stap 2,
omzettingsontwerp). De vertaling per scherm staat in [`docs/ontwerp/PEIL_MIGRATIE.md`](../../ontwerp/PEIL_MIGRATIE.md).
Na elke fase komt er een map `na-fase-1/` en `na-fase-2/` met dezelfde namen, zodat voor en na naast elkaar te leggen zijn.

| Map | Wat | Aantal |
|---|---|---|
| `voor/web/` | web-app (Vite + React 18 PWA), 360/390/768/1440 px × licht/donker, volledige pagina | 222 |
| `voor/web/index.json` | per afdruk: scherm, thema, breedte, URL, h1, horizontale overloop, toelichting | — |
| `voor/web/axe-voor.json` | axe-core 4.14 (WCAG 2.0/2.1/2.2 A+AA) en eigen metingen per scherm (390 en 1440, licht/donker) | — |
| `voor/android/` | native app (Jetpack Compose), 360 en 393 dp × licht/donker; `-volledig` = hele scrolllengte | 68 |

Namen: `<scherm>-<licht|donker>-<breedte>.png`, dezelfde vorm als de Peil-prototypes (`definitief/shots/`).

## Hoe gemaakt (9 november 2026, deze machine)

**Web — tegen de echte app, niet tegen een mock.** Een wegwerp-database in de gedeelde PostgreSQL 16, de echte API
(`shc-api serve`) en de gebouwde web-app achter `vite preview` met `e2e/vite.preview.config.ts`, net als
`apps/web/e2e/run-stack.sh`. Chromium (Playwright 1.65), `deviceScaleFactor` 1, `locale` nl-NL, tijdzone
Europe/Amsterdam, `prefers-color-scheme` licht of donker (de app kent geen eigen themaknop), service worker uit,
`prefers-reduced-motion: reduce`. Volledige pagina: het venster wordt even zo hoog als de pagina, zodat de vaste
ondernavigatie onderaan staat en niets bedekt.

Testgegevens (fictief, alleen in de wegwerp-database): beheerder en kijker, woning "Thuis", twee Gree-units
(woonkamer verwarmt, slaapkamer uit; gedeelde buitenunit), twee Zigbee-sensoren met 24 uur temperatuur,
Hue-groepen (aan, uit, onbereikbaar) met scènes, een P1-meter, kWh-meter airco en boilerstekker met tellerstanden
sinds gisteren 00:00, een tarief, twee meldingen en een weekschema. Er draait **geen worker**: de hartslag van de
achtergronddienst wordt elke 20 s in de database gezet zodat "Achtergronddienst" niet als storing verschijnt; uurtotalen
bestaan daardoor niet (energie komt uit de ruwe tellerstanden en is deels "geschat").

Echte gegevens van de testmachine: de controle "Netwerkdetectie" van de installatiewizard antwoordt met het LAN-adres
van een apparaat in het netwerk van deze machine. Dat adres is op de 8 afdrukken `installatie-*` met een **magenta vak**
afgedekt (`index.json`, veld `opmerking`). Het fictieve thuisadres `192.168.1.20` (uit de bestaande e2e-tests) blijft
zichtbaar. Er zijn geen NAS-gegevens gebruikt.

Toestanden die extra zijn vastgelegd: lege database (beheerder aanmaken), uitgelogd, geen woning, kijker, sensorwaarden
3 uur oud (verouderd), API gestopt (offline-banner na de drempel van de app; klimaat uit de cache; een pagina die nog
nooit geladen was) en "server niet bereikbaar" bij een nieuwe sessie.

**Android — met Paparazzi in een kopie buiten de repo.** De app heeft geen screenshot-tests en hier is geen emulator
bruikbaar (geen toegang tot `/dev/kvm`). Daarom is `apps/android` naar een tijdelijke map gekopieerd, daar met
Paparazzi 1.3.5 en Mockito (alleen testafhankelijkheden in de kopie) uitgebreid, en zijn de **echte composables**
(`HomeScreen`, `ClimateScreen`, `EnergyScreen`, `MoreScreen`, `PairScreen`, `UnpairedScreen`) gerenderd met
`HuisTheme`. De schil (Scaffold + NavigationBar) is een letterlijke kopie van `HuisRoot`, omdat die de gekozen tab
intern bewaart. Gegevens: JSON van dezelfde echte API en testgegevens als hierboven (`overview`, `energy/summary`,
`schedule`), gedecodeerd met de eigen `ShcJson` van de app. In de repo is niets aan de Android-app veranderd.

Beperkingen van de Android-afdrukken (eerlijk benoemd):
- Geen echte telefoon of emulator: lettertype, statusbalk en randen kunnen afwijken. **NIET GEVALIDEERD (hardware).**
- De ViewModel is een nep-object: wat de app pas ná een netwerkverzoek toont (bijv. het weekschema van de slaapkamer:
  "Weekschema laden…"), blijft in die toestand staan. De camera start niet in Paparazzi ("De camera kan niet worden
  gestart") — dat is wel een echte toestand van het koppelscherm.
- Paparazzi verkleint elke afdruk tot maximaal 1000 px; `-volledig` is daarom uit drie delen samengesteld en de lege
  ruimte tussen inhoud en ondernavigatie is weggeknipt.
- De widget (Glance) is niet vastgelegd; die wordt in de migratie alleen beschreven.

**Verkleining.** Alle PNG's hebben een palet van 256 kleuren waarin de 96 meest voorkomende kleuren (vlakken, kaarten,
randen, tekst) exact behouden blijven; alleen randpixels van letters en iconen zijn afgerond. Elke kleur die ≥ 0,05 %
van de pixels beslaat is na verkleining gecontroleerd op exacte gelijkheid. De afdrukken zijn een beeldverslag, **geen
meetbasis**: contrastwaarden in de migratie komen uit de CSS/Kotlin-waarden, niet uit deze PNG's.

## Opnieuw maken

De hulpscripts en hun vereisten staan in [`werkwijze/README.md`](werkwijze/README.md) (alleen documentatie; CI
gebruikt ze niet). Kort: kopieer `werkwijze/` naar een map buiten de repo en draai daar

```sh
./run_shots.sh <ruwe map>                          # web-afdrukken (PEIL_WIDTHS=390 voor een proefronde)
PEIL_SCRIPT=fixtures.mjs ./run_shots.sh <map>      # JSON voor de Android-afdrukken
PEIL_SCRIPT=axe.mjs      ./run_shots.sh <map>      # axe-nulmeting
python nabewerk.py <ruwe map> <doelmap>            # verkleinen (Android: ook samenvoegen en inkorten)
```
