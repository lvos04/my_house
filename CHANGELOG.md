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

### Gerepareerd

- **Database-update 0004 hangt niet meer af van de tijdzone van de database.** De vervaldatum van oude
  telefoonsleutels (180 dagen na het laatste gebruik, minstens 30 dagen na de update) is nu precies 180 × 24 uur,
  net als bij een nieuwe koppeling. Voorheen kon die op een database buiten UTC een uur verschuiven rond de
  zomer- en wintertijd. De test draait de update nu in drie tijdzones. Zie `docs/adr/ADR-0007-tijdzone-onafhankelijke-migraties.md`.
