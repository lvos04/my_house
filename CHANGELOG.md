# Wijzigingen

Wat er per versie verandert. De uitgebreide uitleg per release staat in `docs/release-notes.md`.

## Unreleased

### Uiterlijk (web-app naar de ontwerptaal Peil)

- **Nieuw uiterlijk volgens Peil 1.1.0**, in twee stappen: eerst de aliaslaag (alle kleuren, radii en de focusring),
  daarna Tailwind 4 + shadcn/ui (React 18-variant) + lucide-iconen. Koel neutrale ondergrond, systeemlettertype,
  blauwe primaire knop, randen die ook in de zon zichtbaar zijn, tekst minstens 14 px en knoppen minstens 44 × 44 px.
  De zijbalk verschijnt vanaf 1024 px; daaronder kopbalk en ondernavigatie.
- **Status altijd als icoon + woord + vorm**: Gezond · Beperkt · Storing · Onbekend (met reden), meldingen met een
  ernstlabel (Info · Middel · Hoog), "Alleen meten" bij de boiler zonder schakelaar. Groen alleen nog voor gemeten
  gezondheid; verbonden, gekoppeld, klaar en gemeten zijn neutraal. Offline en verouderd worden gestippeld en
  gearceerd in plaats van vaag (geen doorzichtigheid meer op tekst).
- **Grafieken** in het gevalideerde Peil-palet (airco, boiler en overig ook in donker goed te onderscheiden);
  onvolledige dagen gestippeld en gearceerd in plaats van oranje.
- Teksten: "Let op" heet nu **"Wat actie nodig heeft"**, een systeemfout **"Storing in het systeem"**, meetkwaliteit
  "ontbreekt" heet **"geen meting"**. Teksten met domeinbetekenis (boilerblokkade, gedeelde buitenunit, offline-banner)
  zijn gelijk gebleven.
- Toegankelijkheid: lichttegels en de knoppen "Gezien" en Aan/Uit hebben een naam die met de zichtbare tekst begint
  (axe 0 op alle hoofdschermen in licht en donker).

### Uiterlijk (Android-app naar de ontwerptaal Peil)

- **Zelfde beeldtaal als de web-app**: kleuren en typografie komen uit de Peil-tokens (licht en donker volgen het
  systeem), lucide-iconen in plaats van Material-iconen, kaarten met een rand in plaats van een schaduw, hoeken van
  8 dp, tekst minstens 14 sp en knoppen minstens 48 dp hoog. Primaire knoppen zijn blauw.
- **Status altijd als icoon + woord + vorm** (Gezond · Beperkt · Storing · Onbekend · Verouderd), meldingen met een
  ernstlabel (Info · Middel · Hoog), de opdrachtregel met een eigen icoon per uitkomst. "Let op" heet nu
  **"Wat actie nodig heeft"**, een systeemfout **"Storing in het systeem"** en meetkwaliteit "ontbreekt" heet
  **"geen meting"**, net als in de web-app.
- **Geen verbinding**: de app dimt niets meer; elke kaart krijgt een gestippelde rand en "Verouderd · Laatst bekend ·
  15:47". Ook de widget toont "Verouderd" met een icoon in plaats van grijze tekst.
- Boiler en verwarmen hebben eigen kleuren in de grafiek (waren in donker bijna gelijk); "Thuis", "gemeten" en
  "Opgeslagen" zijn niet meer groen. Het koppelscherm volgt nu het thema van de telefoon.

### Uiterlijk (onafhankelijke ontwerpreview, stap 5)

- **Zonder verbinding is niets meer groen**: een laatst bekende "Gezond" heet offline "Verouderd" (web en Android);
  Beperkt en Storing blijven staan. Offline krijgen de kaarten weer de gestippelde Verouderd-rand. Zie
  `docs/adr/ADR-0011-peil-review-verouderd-en-nooit-opgehaald.md`.
- **Een pagina die nog nooit was opgehaald** toont zonder verbinding "Onbekend — nog niet opgehaald" met de reden in
  plaats van eindeloos "Laden…", en heeft ook tijdens laden of bij een fout een paginakop.
- Het vinkje van een gekozen modus ("✓ Verwarmen") en de naam van een lichttegel op 320 px blijven binnen hun knop;
  op een telefoon schuift de focus in het weekschema niet meer onder de opslaanbalk.
- De grafiek "Laatste 24 uur" heeft graden en tijden op de assen; een offline apparaat heeft een goed zichtbare
  stippelrand; decoratieve scheidingslijnen zijn doorgetrokken (stippel betekent alleen nog onbekend of verouderd).
- Koppen zonder sprongen en `aria-controls` alleen naar een bestaand paneel (alleen voor schermlezers merkbaar); een
  back-up die er staat heet "Gemaakt" in plaats van "Gelukt".

### Toegevoegd

- **Themaknop Systeem / Licht / Donker** in de zijbalk en het menu "Meer" (alleen op dit apparaat bewaard).
- **Link "Terug naar de Hub"** in de zijbalk en het menu "Meer", alleen zichtbaar als de webcontainer een geldig
  Hub-adres krijgt via de optionele omgevingsvariabele `HUB_URL` (zonder die variabele verandert er niets).

### Tests

- Android: schermafdruktests met Paparazzi (licht en donker, 360 en 393 dp; CI vergelijkt ze met de vastgelegde
  beelden), unittests voor de Peil-statusvorm, het contrast van alle gebruikte kleurparen en de tokens tegen
  `design/ontwerptaal/tokens.json`.
- Browsertests met axe-core op de hoofdschermen (licht en donker, 390 en 1440 px) en de Peil-matrix van 320 tot
  1440 px; unittests voor thema, Hub-adres en de nieuwe componenten; infra-unittest voor het startscript van de
  webcontainer.
- Ontwerpreview: de Peil-matrix controleert ook dat inhoud binnen zijn knop of tegel blijft; een volledige
  Tab-doorloop (390 en 1440 px, licht en donker) eist een focusring van minstens 3:1 die nooit achter een vaste balk
  ligt; de offline-test eist "nooit groen" en Onbekend voor een nooit opgehaalde pagina.
- Controle (QA-review): de offline-browsertest zet nu zelf een verse hartslag van de achtergronddienst (testgegevens),
  zodat er online echt een gemeten "Gezond" staat en "nooit groen offline" iets toetst, ook als de test los draait
  (zonder die hartslag stond er online geen enkel Gezond en slaagde de controle altijd). Tegen een build zonder de
  offline-regel faalt de test nu aantoonbaar.
- OpenAPI-driftcontrole (contract §5): `apps/api/tests/test_openapi_snapshot.py` faalt als `create_app().openapi()`
  afwijkt van `docs/openapi/my_house.openapi.json`. Dit is het driftdeel van P3-10; de rest van P3-10 staat nog open.

### Documentatie

- **Omzettingsontwerp naar de ontwerptaal Peil**:
  `docs/ontwerp/PEIL_MIGRATIE.md` vertaalt elk scherm van de web-app en de Android-app naar Peil (tokens, componenten,
  iconen, statusvorm, microcopy), beschrijft fase 1 (aliaslaag) en fase 2 (Tailwind 4 + shadcn/ui in de React
  18-variant), de link "Terug naar de Hub", de themaknop en welke tests geraakt worden. Besluit in
  `docs/adr/ADR-0008-peil-omzetting-twee-fasen.md`; de bouwkeuzes van fase 2 in
  `docs/adr/ADR-0009-peil-fase-2-tailwind-shadcn-react18.md`.
- Nulmeting: schermafdrukken van de huidige stand (web 222, Android 68) en een axe-meting in
  `docs/screenshots/peil/voor/`; afdrukken en axe-meting ná de omzetting in `docs/screenshots/peil/na/`.
- Versie-gepinde kopie van Peil 1.1.0 met herkomst en sha256 in `design/ontwerptaal/`.
- Android-app naar Peil: `docs/adr/ADR-0010-peil-android-compose.md`, uitvoering in `docs/ontwerp/PEIL_MIGRATIE.md`
  §15, afdrukken in `docs/screenshots/peil/android/`.
- Onafhankelijke ontwerpreview tegen Peil: `docs/ontwerp/PEIL_REVIEW.md` (bevindingen, metingen in twee rondes,
  vergelijking met de prototypes, open punten), samenvattingen in `docs/screenshots/peil/review/`.

### Gerepareerd

- **Startscript van de webcontainer (`HUB_URL`)** weigert nu ook andere schrijfwijzen van loopback- en lege adressen,
  zoals `0177.0.0.1`, `2130706433`, `0x7f.1`, `[0::1]` en `[0:0:0:0:0:ffff:7f00:1]`. Een IPv4-adres mag alleen in de
  gewone vorm a.b.c.d. De browser verborg de link in die gevallen al (`src/hub.ts` controleert met de URL-parser);
  nu staat zo'n adres ook niet meer in `/_shc/config.json` en meldt het logboek waarom het genegeerd is.
- **Database-update 0004 hangt niet meer af van de tijdzone van de database.** De vervaldatum van oude
  telefoonsleutels (180 dagen na het laatste gebruik, minstens 30 dagen na de update) is nu precies 180 × 24 uur,
  net als bij een nieuwe koppeling. Voorheen kon die op een database buiten UTC een uur verschuiven rond de
  zomer- en wintertijd. De test draait de update nu in drie tijdzones. Zie `docs/adr/ADR-0007-tijdzone-onafhankelijke-migraties.md`.
