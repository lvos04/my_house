# Wijzigingen

Wat er per versie verandert. De uitgebreide uitleg per release staat in `docs/release-notes.md`.

## Unreleased

- Veiligheid (V-094): een databasefout toont de waarden van gebonden parameters niet meer in de foutmelding of de log (`hide_parameters` op alle engines van de API, CLI, back-up en migraties).
- Veiligheid (KLEIN-31): een databasefout bij herstel, back-up of database-dump toont alleen nog sqlstate, constraint en tabel, niet de DETAIL-regel van PostgreSQL (die kan een sleutelwaarde of een hele rij met een hash bevatten).
- Veiligheid (KLEIN-37): een onverwachte databasefout in de API of een worker-lus komt in de log alleen nog met soort, sqlstate, constraint en tabel; een logfilter op de handlers van de app en van uvicorn haalt als vangnet de DETAIL-regel van PostgreSQL weg. Het HTTP-antwoord blijft 500 "Internal Server Error".
- Veiligheid (KLEIN-38): als het starten van een handmatige back-up of een herstel-job faalt door een databasefout, tonen het HTTP-antwoord, de foutmelding van de job en de log alleen nog soort, sqlstate, constraint en tabel, niet de DETAIL-regel van PostgreSQL; andere fouten houden hun tekst.
- Veiligheid (KLEIN-39): ook bij een databasefout in de start van een automatische back-up, een melding van de klimaatregeling, het detail van een mislukt commando en "Database-update mislukt" van `shc-api migrate` staan alleen nog soort, sqlstate, constraint en tabel, niet de DETAIL-regel van PostgreSQL; andere fouten houden hun tekst. De gedeelde hulpfunctie `fouttekst` staat nu in `shc_api/logfilter.py`.
- Veiligheid (KLEIN-42): de API weigert bij het opstarten ook een te korte `SHC_SECRET_KEY` of `SHC_SECRET_KEY_FILE` (minder dan 32 tekens), op dezelfde manier als een ontbrekende: een RuntimeError met een vaste Nederlandse tekst zonder (een deel van) de sleutel, vóór de achtergrondtaken starten. Routes draaien dus niet meer met een sleutel die SecretBox weigert.
- Herstel (KLEIN-40): `shc-api verify-backup` en `shc-api restore` stoppen niet meer met een traceback als SHC_SECRET_KEY te kort is; de sleutelcontrole meldt dan "Apparaatsleutels niet gecontroleerd: SHC_SECRET_KEY is ongeldig (moet minimaal 32 tekens zijn).", zonder (een deel van) de sleutel, en het commando loopt verder zoals bij een ontbrekende sleutel.
- Herstel (KLEIN-41): bij het controleren van een back-up via de web-app (`POST /system/restore/verify`) geeft een ontbrekende of te korte `SHC_SECRET_KEY` nu 503 `secret_key_invalid` met een vaste tekst zonder (een deel van) de sleutel, in plaats van een kale 500; het geüploade bestand wordt opgeruimd.
- Veiligheid (KLEIN-43): omdat `SHC_SECRET_KEY_FILE` bij elke aanroep opnieuw wordt gelezen, kan de sleutel tijdens het draaien ontbreken of te kort zijn; `PUT /system/backup-settings` met wachtwoordzin, `GET`/`PUT /system/notify`, `POST /system/notify/test` (met een adres in de app), `POST /devices/{id}/test` (koppeling met inloggegevens) en `POST /integrations/{provider}/pair` geven dan 503 `secret_key_invalid` met dezelfde vaste tekst als de restore-controle, zonder iets op te slaan, in plaats van een kale 500, de melding "andere hoofdsleutel", 409 of `key_mismatch`; koppelen controleert de sleutel vóór het apparaat wordt aangesproken.
- Veiligheid (KLEIN-44): de vaste tekst bij 503 `secret_key_invalid` luidt nu "De sleutel van deze server ontbreekt of is ongeldig; deze actie is niet mogelijk." (ook bij opslaan, meldingen en koppelen); de achtergrondcontrole logt bij een ontbrekende of te korte sleutel een eigen waarschuwing in plaats van "andere hoofdsleutel".
- Veiligheid (KLEIN-45): een koppelcode voor een telefoon (8 tekens) staat in `pairing_codes.code_hash` niet meer als kale SHA-256 maar als HMAC-SHA256 met een sleutel die met HKDF (context `shc-pairing-code-v1`) uit `SHC_SECRET_KEY` wordt afgeleid; een gelekte hash is zonder de serversleutel niet meer terug te rekenen. Geen schemawijziging. Codes die vóór de update zijn gemaakt werken daarna niet meer (ze verlopen toch na 10 minuten): maak een nieuwe QR-code. `POST /pairing/codes` en `POST /pairing/redeem` geven 503 `secret_key_invalid` als de sleutel ontbreekt of te kort is.
- Veiligheid (KLEIN-46): het veld `error` van `GET`/`PUT /system/notify` (`notify.stored_url_error`) zegt bij een ontbrekende of te korte sleutel nu met een vaste tekst zonder sleutelmateriaal dat de sleutel ontbreekt of ongeldig is, in plaats van "andere hoofdsleutel" of een RuntimeError; bij een geldige maar andere sleutel blijft de oude tekst.
- Veiligheid (KLEIN-53): een databasefout in `shc-api backup`, `restore`, `verify-backup`, `restore-drill`, `rekey`, `reset-password`, `maintenance` of `decrypt-dump` geeft geen traceback met de DETAIL-regel van PostgreSQL meer op stderr, maar één regel "Opdracht <naam> mislukt: <soort>: <fouttekst>" (alleen soort, sqlstate, constraint en tabel) met exitcode 1, zoals voorheen; het logfilter hangt bij deze commando's ook aan `logging.lastResort`, zodat een waarschuwing (bijv. "Status niet doorgegeven aan open schermen") de DETAIL-regel niet meer toont. Andere fouten gaan ongewijzigd door.
- Tests (KLEIN-62): de screenshot-scripts in `docs/screenshots/peil/werkwijze/` hebben geen eigen kopie meer van de testaccounts; `testaccounts.mjs` leest ze uit `apps/web/e2e/helpers.ts`, zodat de testwaarden op één plek staan. Geen ander gedrag.
- Docs (KLEIN-64): de OpenAPI-momentopname (`docs/openapi/my_house.openapi.json`) noemt nu 503 `secret_key_invalid` bij de negen routes die dat antwoord kunnen geven (via `responses=` op de routes); een nieuwe test bewaakt dat. Geen ander gedrag.
- Web (KLEIN-70): de web-app heeft een eigen vangnettekst voor `secret_key_invalid` ("De sleutel van deze server ontbreekt of is ongeldig; deze actie is niet mogelijk.", gelijk aan de vaste servertekst); die verschijnt alleen als de server geen tekst meestuurt. Geen ander gedrag.

### Grafiek "Laatste 24 uur": vegen en slepen op een telefoon (technische review, 2026-11-11)

- **Vegen over de grafiek kiest geen uur meer.** Wie op een telefoon over de grafiek veegt om de pagina te schuiven,
  kreeg een uur gekozen (draadkruis en uitlezing) dat met een tik ernaast niet meer wegging. Nu schuift de pagina en
  blijft de vorige keuze zoals hij was.
- **Na slepen over de uren wist een tik ernaast de keuze,** net als na een tik.
- **Knijpen met twee vingers op de grafiek zoomt de pagina in.** Begon het gebaar op de grafiek, dan zoomde de
  pagina niet; dat kan nu ook daar. Horizontaal slepen kiest nog steeds een uur.
- **Knijpen kiest geen uur.** Alleen de eerste vinger kiest een uur (tikken of horizontaal slepen); na het knijpen
  staat de keuze van daarvoor er weer, of geen. (In de eerste versie van het knijpen bleef daarna het uur onder de
  eerste vinger gekozen, en wiste een tik ernaast het niet; de technische review vond dat.)
- Alleen gedrag bij aanraken; uiterlijk, teksten, API en gegevens blijven gelijk. Getest met het nagebootste
  aanraakscherm van Chromium; een echte telefoon is niet getest (NIET GEVALIDEERD (hardware)).

### Grafiek "Laatste 24 uur": verouderd zichtbaar, bron en tijd erbij (ontwerpreview, 2026-11-11)

- **Een verouderde sensor is nu te zien in de grafiek.** Stuurt de sensor geen metingen meer (de server noemt hem dan
  verouderd), dan stopt de lijn bij de laatste meting. Het stuk daarna is gearceerd met een stippelrand, en onder de
  grafiek staat "Verouderd" met de laatst bekende waarde en tijd. Eerder hield de grafiek gewoon op bij de laatste
  meting en zag hij eruit als actueel.
- **Bron en tijd onder de grafiek:** bijvoorbeeld "Bron: sensor van de airco · bijgewerkt 11 nov 08:29".
- **De laatste meting is een stip;** op een breed scherm staat de waarde ernaast. De ruimte daarvoor volgt de breedte
  van het getal, zodat het in elk lettertype en ook bij "-10,5°" of "100,0°" binnen de grafiek blijft. (In de eerste
  versie van deze review was die ruimte vast en stak het getal in een breder lettertype 3,5 px buiten de grafiek; de
  automatische controle vond dat.)
- **Hele uren op de tijdas** op een breed scherm (was alleen begin en eind).
- Kleiner: de doellijn loopt niet door in het verouderde stuk; de legenda staat er alleen als er een grafiek is; de
  getallen langs de as vallen binnen het plotvak, zodat de focusring er niet meer doorheen loopt; bij één meting staat
  de tijd één keer onder de as (stond er twee keer).
- Alleen weergave, teksten en toegankelijkheid: geen nieuwe API, instelling of database-update. Nagekeken tegen de
  echte dienst met testgegevens in Chromium op 320–1440 px, licht en donker, met twee systeemlettertypen (axe-core 0).
  Een echte schermlezer, echte telefoons, Firefox en Safari zijn niet getest (NIET GEVALIDEERD (hardware)); andere
  systeemlettertypen dan Noto Sans en DejaVu Sans ook niet. ADR-0014 en `docs/ontwerp/PEIL_REVIEW.md` §12.

### Grafiek "Laatste 24 uur": tabel per uur en toetsenbord (2026-11-11)

- **"Toon als tabel, per uur"** onder de grafiek bij Klimaat › Bediening: per klokuur het gemiddelde, de laagste en
  hoogste meting en het doel. Op een telefoon is elke rij een kaart.
- **Met het toetsenbord:** de grafiek is één Tab-stop; pijltje rechts/links leest per uur, Home en End springen naar
  het eerste en laatste uur, Esc stopt. De waarde staat in één regel onder de grafiek en wordt ook voorgelezen. Wie
  een schermlezer gebruikt, leest dezelfde gegevens in de tabel.
- **Met muis of vinger:** aanwijzen of tikken kiest hetzelfde uur (een band over de grafiek, geen zwevend venstertje).
  Na een tik blijft de keuze staan tot je ernaast tikt; met een muis verspringt de pagina niet tijdens het aanwijzen.
- **Eerlijk over gaten:** een uur zonder meting heet overal "Geen meting". De lijn breekt daar (werd doorgetrokken) en
  een stuk zonder enige meting is gearceerd. Een meting zonder waarde telde als 0 in de schaal; dat is weg.
- Alleen weergave en toegankelijkheid (Peil 1.1.1 §10, besluit van de gebruiker): geen nieuwe API, instelling of
  database-update; de Android-app heeft deze grafiek niet. Getest in Chromium op 320–1440 px, licht en donker
  (axe-core 0); een echte schermlezer en echte telefoons zijn niet getest (NIET GEVALIDEERD (hardware)). ADR-0013.

### Platformcontract 1.3 en Peil 1.1.1 (conformiteitsronde, 2026-11-10)

- **Contractkopie 1.3** (was 1.1): `docs/platform-v2/PLATFORM_CONTRACTEN.md` en `CONTRACT_1.3_CONFORMITEIT.md`
  byte-gelijk uit ai-server-manager, met pin (`CONTRACT_HERKOMST.json`) en drifttest (`tools/contract_kopie.py`, in CI
  ook tegen GitHub). `EVIDENCE.json` volgt §11 van 1.3, bewaakt door een nieuwe test.
- **"Terug naar de Hub" (`HUB_URL`)** volgt de adresregels van contract 1.3 §3.2, in het startscript van de
  webcontainer én in de app, met dezelfde 103 gevallen. Nu ook geweigerd: `[::127.0.0.1]`, `localhost.` (in de app),
  niet-canonieke IPv4 als `10.1` of `010.0.0.1` (in de app), meerregelige waarden, niet-ASCII, lege labels en poorten
  met voorloopnul. Een geldig adres verandert niet; een ongeldig adres verbergt alleen de link.
- **Ontwerptaal Peil 1.1.1** (was 1.1.0) als gepinde kopie met bron-commit (`tools/peil_kopie.py`, regel E08):
  schemabanden van verwarmen en koelen met tekst ≥ 7:1, de rand van een gekozen modus en een brandende lichttegel in de
  donkere inkt, ondernavigatie op de breedte van de labels en zonder vet actief item (web en Android; "Overzicht" stak op
  320 px 2 px uit zijn kolom). Functies, routes, teksten en API zijn gelijk.
- Delta-matrix: geen enrollment via `HUB_URL` en geen push naar de Hub-inbox (contract 1.3 §2.3); ADR-0012.
- Review van de conformiteitsronde: een statische test bewaakt dat `HUB_URL` nergens een koppeling start (geen
  enrollment-token, vingerafdruk, koppelbestand of inbox-push in de broncode; het startscript leest alleen `HUB_URL`; de
  web-app haalt het adres alleen uit `/_shc/config.json`). CI controleert nu ook het startscript van de webcontainer met
  shellcheck. De installatiehandleiding en de README noemen de bestaande route (shell, `install.sh`, *Install via YAML*)
  nu eerlijk als route van vóór het platform; de standaard Custom App zonder terminal komt in P3 (C1.3-MH-03).
- **Laatst bekende gegevens na herladen:** gegevens uit de opgeslagen momentopname ("laatst bekend") worden bij de
  start nu altijd meteen opnieuw opgehaald. Was de momentopname jonger dan 10 seconden, dan gold hij als vers: na een
  snelle herlaad kende de app bijvoorbeeld een net gemaakte woning niet (gevonden doordat de browsertest "eerste
  start" in CI-run 42785063693 faalde). CI bewaart het rapport en de traces van mislukte browsertests nu ook echt (de
  map `.output` werd als verborgen map overgeslagen).

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

### Licentie en opruimen (openbaar maken)

- **Licentie: GPL-3.0** (`LICENSE`, besluit van de eigenaar, 2026-11-09); de README noemt hem.
- Het gecommitte wheel `shellcheck_py-0.11.0.1-…whl` (3,8 MB, ShellCheck is GPL-3.0) staat niet meer in de root
  (P3-14). Niets verwees ernaar; CI gebruikt de ShellCheck van de runner.
- De hulpscripts in `docs/screenshots/peil/werkwijze/` bevatten geen paden van de ontwikkelmachine meer. Repo,
  Python, PostgreSQL, Playwright en axe-core komen uit `PEIL_REPO`, `PEIL_PYTHON`, `PEIL_PG`, `PEIL_PLAYWRIGHT` en
  `PEIL_AXE`; zonder die variabelen gebruiken de scripts de pakketten van `apps/web`. App, database en afdrukken
  veranderen niet.

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
