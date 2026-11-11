# ADR-0013 — Grafiek "Laatste 24 uur": tabel per uur en bediening met het toetsenbord

- **Status:** aangenomen (2026-11-10); aangevuld op 2026-11-11 (aanraken, vaste plaats voor de uitlezing,
  schermlezer)
- **Context-ID's:** geen eigen Excel-ID (uiterlijk en toegankelijkheid); besluit van de gebruiker na contract 1.3
  ("Extra's (C09): my_house krijgt een tabelweergave + toetsenbordbediening bij de grafiek 'Laatste 24 uur'",
  `ai-server-manager/docs/platform-v2/OVERDRACHT/STATUS.md` en `02_BOUWPLANNEN/B03_BESLUITEN.md`);
  `CONTRACT_1.3_CONFORMITEIT.md` §8 (repowerk C09); [`docs/ontwerp/PEIL_MIGRATIE.md`](../ontwerp/PEIL_MIGRATIE.md) §7 en
  §13 punt 6; Peil 1.1.1 §10 (interactie, eerlijkheid) en §15 punt 8 (`design/ontwerptaal/`); ADR-0008 t/m ADR-0012.

## Context

De lijngrafiek "Laatste 24 uur" (Klimaat › Bediening) toont de gemeten temperatuur en het doel. Peil §10 vraagt bij
elke grafiek een tabel als WCAG-tweeling en bediening met het toetsenbord (plotvak `tabindex=0`, pijltjes, Home/End,
Esc, voorlezen via `aria-live`). Bij de omzetting naar Peil was dat voor my_house alleen een aanbeveling, omdat het een
functionele toevoeging is (PEIL_MIGRATIE §13 punt 6, ONTWERPTAAL §15 punt 8). De gebruiker heeft het op 10 november
2026 toegestaan.

De API geeft per zone de ruwe metingen van de laatste 24 uur (`GET /zones/{id}/history`). De Gree-worker leest elke
30 s; dat zijn tot ± 2 900 punten per reeks. Een tabel of een pijltjestoets per ruw punt is dan onbruikbaar. Een meting
kan ook zonder waarde zijn (`value` is in het datamodel nullable); de oude grafiek rekende zo'n `null` als 0 in de
schaal en trok de lijn door over uren zonder meting.

## Besluit

1. **Per klokuur.** De tabel en het toetsenbord werken per uur, van het uur van het eerste punt tot en met het uur van
   het laatste (meestal 24 of 25 rijen). Per uur: het **gemiddelde**, de **laagste en hoogste** meting en het **doel**
   (verandert het doel binnen het uur, dan alle waarden in volgorde: "21,0° → 19,0°"). Het eerste en laatste uur tonen
   het deel dat de grafiek beslaat ("07:10–08:00", "13:00–13:10"), met de datum erbij, want 24 uur loopt over
   middernacht. Begint en eindigt zo'n deel in dezelfde minuut (de laatste meting valt precies op het hele uur), dan
   staat de tijd er één keer ("10 nov 10:00", niet "10:00–10:00"). Uren rekenen in UTC-uren: in elke tijdzone met hele
   uren verschil (Europe/Amsterdam, ook rond de zomertijd) is dat het klokuur; in een tijdzone met een half uur
   verschil zijn de rijen "09:30–10:30" (juist, alleen niet op het hele uur).
2. **Eén begrip "Geen meting", overal hetzelfde.** Een uur zonder meting met waarde heet in de tabel en in de
   voorleesregel "Geen meting" (nooit 0, nooit "–"). De lijn **breekt** op precies die uren en bij een meting zonder
   waarde; een stuk zonder enige meting (geen temperatuur en geen doel) is gearceerd (`arcering-gat`), met het woord
   erin als het helemaal past (containerquery: anders weg, nooit half) en dan "Gearceerd: geen meting (nooit 0)." onder
   de grafiek. Een losse meting tussen twee gaten wordt een stip (8 px, ring 2 px in de kaartkleur). Zo zeggen lijn,
   tabel en voorleestekst hetzelfde (Peil §10, eerlijkheid; PEIL_MIGRATIE §7 noemde het gat al voor deze grafiek).
   Een `null` telt niet meer mee in de schaal. De **doellijn** is een traplijn die per stuk doorloopt tot het eind van
   het uur van zijn laatste waarde (of het eind van de grafiek) en dus nooit over een uur zonder doel heen: een uur dat
   in de tabel "Geen meting" heeft voor het doel, heeft in de grafiek ook geen doellijn. Is er wel een gemeten
   temperatuur maar geen doel (of andersom), dan is dat uur **niet** gearceerd: de arcering betekent "helemaal niets
   gemeten", de ontbrekende reeks is te zien aan de onderbroken lijn en te lezen in de tabel en de leesregel.
3. **Plotvak als één Tab-stop** (`role=img`, `tabindex=0`). De toegankelijke naam blijft de bestaande samenvatting
   ("Gemeten en doeltemperatuur, bereik … tot …, laatste meting …", nu de laatste meting mét waarde); de uitleg van de
   toetsen is de **beschrijving** (`aria-describedby`) en staat zichtbaar onder de grafiek zolang het plotvak de focus
   heeft en er nog geen uur gekozen is. **→** begint bij het eerste uur, **←** bij het laatste, **Home/End** springen,
   **Esc** stopt (en is vrij als er niets gekozen is). Andere toetsen (Tab, Enter) gaan gewoon door: geen focusval.
   Focus weg = keuze weg. **Schermlezers:** het plotvak is een afbeelding met een naam en een beschrijving. In de
   leesmodus van een schermlezer (NVDA, JAWS) gaan de pijltjes naar de schermlezer zelf en niet naar de pagina; daarom
   zegt de beschrijving dat de tabel dezelfde gegevens bevat. De tabel is dus de weg voor wie een schermlezer gebruikt,
   de pijltjes zijn de weg voor wie het toetsenbord gebruikt en het scherm ziet. Dit is hetzelfde patroon als de
   lijngrafiek van de Hub (`ai-server-manager/apps/web/src/components/LineChart.tsx`); geen `role="application"`, want
   dat zet de leesmodus voor het hele vak uit.
4. **Draadkruis per uur en één leesregel.** Het gekozen uur is een band met inktranden over de volle hoogte. De waarde
   staat in één zichtbare regel onder de grafiek, die ook wordt voorgelezen (`aria-live="polite"`): "10 nov
   12:00–13:00: gemeten gemiddeld 20,8° (laagst 20,5°, hoogst 21,0°); doel 21,0° en daarna 19,0°". Geen zwevende
   tooltip: die zou de lijn bedekken en op een telefoon buiten beeld vallen. De **aanwijzer** (ook een tik) kiest
   hetzelfde uur; een waarde is dus nooit alleen met de muis of alleen met kleur te lezen. Met een **muis** vervalt de
   keuze zodra de aanwijzer het plotvak verlaat (tenzij het plotvak de focus heeft). Na een **tik** op een aanraakscherm
   blijft de keuze staan tot een tik ernaast of Esc: een vinger "verlaat" het vak op het moment van loslaten, en anders
   zou de uitlezing meteen weer verdwijnen. Op een apparaat met een muis (`@media (hover: hover)`) is de plaats van de
   leesregel vooraf zo hoog als de langste uitlezing van deze grafiek, zodat "Toon als tabel" en alles eronder niet
   verspringt terwijl de aanwijzer over de grafiek gaat (zonder die ruimte sprong het op 320 px 20–80 px, gemeten in
   de browsertest). Op een aanraakscherm is die ruimte er niet: daar verschijnt de regel alleen na een bewuste tik.
5. **"Toon als tabel, per uur"** als uitklapblok onder de grafiek, net als "Toon als tabel" bij Energie: `caption`,
   `th scope`, tabelcijfers; op een telefoon wordt elke rij een kaart (`stack-mobile`, < 641 px). Dicht bij het openen
   van de pagina.
6. **Alleen weergave.** Geen nieuwe API, geen nieuw datamodel, geen nieuwe instelling. Het TypeScript-type
   `ZoneHistory` staat nu `number | null` toe, zoals de API al kon sturen. De Android-app heeft deze grafiek niet; daar
   verandert niets.

## Gevolgen

- Code: `apps/web/src/climate/history.ts` (per uur, lijnstukken, gaten, teksten) en
  `apps/web/src/climate/HistoryChart.tsx` (grafiek en tabel; `pages/Climate.tsx` exporteert hem verder voor bestaande
  imports); stijl bij de haakklassen `hist-*` in `src/peil/componenten.css`.
- Tests: `src/test/history-chart.test.tsx` (vitest, 19 tests: per uur, `null` nooit 0, lijn breekt, toetsen, muis en
  tik, vaste plaats voor de uitlezing, tabel = toetsenbord, duizenden metingen zonder stapeloverloop); browsertest
  `e2e/12-grafiek-24-uur.e2e.ts` (4 tests) tegen de echte API met metingen zoals de worker ze opslaat
  (`e2e/seed.py readings`): Tab-stop met ring, ← → Home End Esc, aanwijzer, tabel met "Geen meting", axe-core 0, een
  tik op een telefoon (aanraakscherm, 390 px) en de Peil-matrix (320–1440 px, licht en donker) met open tabel en
  gekozen uur, waarin op elke breedte ook gemeten wordt dat "Toon als tabel" niet verspringt als de muis over de uren
  gaat. De metingen van de Peil-tests staan nu in `e2e/peil-metingen.ts` (gedeeld door 11 en 12).
- Bewijs: `docs/platform-v2/EVIDENCE.json`, regel `REVIEW-my_house-grafiek-24-uur` (status Bezig; er is geen Excel-ID
  en geen contractpunt voor dit repowerk, zie hieronder).
- Open bij de bron (ai-server-manager, niet in deze repo te wijzigen): de gepinde kopieën
  `CONTRACT_1.3_CONFORMITEIT.md` §8 en Peil §15 punt 8 noemen de tabel bij my_house nog als punt dat akkoord vraagt;
  het akkoord is er (10 november 2026). Contract §11 kent geen ID-reeks voor repowerk zonder Excel-ID of contractpunt;
  daarom een `REVIEW-my_house-…`-regel (de veiligste lezing: geen nieuw soort ID verzinnen).
- Bekend en niet in deze stap: de Peil-markering "verouderd na het laatste punt" en het eindpunt met direct label
  (PEIL_MIGRATIE §7) — de x-as loopt tot het laatste punt, dus er is geen vlak na het laatste punt; een echte
  schermlezer (NVDA, TalkBack, VoiceOver) en echte apparaten zijn niet getest (**NIET GEVALIDEERD (hardware)**): de
  tik is getest met het nagebootste aanraakscherm van de testbrowser (Sim-getest), niet met een echte telefoon.
- Later (11 november 2026): de onafhankelijke ontwerpreview heeft de markering "verouderd" en het eindpunt alsnog
  gebouwd, met een `<figure>` met bron en tijd en hele uren op de tijdas. Zie
  [ADR-0014](ADR-0014-grafiek-24-uur-ontwerpreview-verouderd-en-figuur.md). De aantallen tests hierboven zijn die van
  deze stap; sindsdien zijn het er 38 (vitest) en 6 (browsertest).
