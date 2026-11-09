# ADR-0011 — Peil-review: verouderd is nooit Gezond, nooit opgehaald is Onbekend

- **Status:** aangenomen (2026-11-09, stap 5: onafhankelijke ontwerpreview)
- **Context-ID's:** geen eigen Excel-ID (uiterlijk en teksten); ADR-0008, ADR-0009 en ADR-0010;
  [`docs/ontwerp/PEIL_REVIEW.md`](../ontwerp/PEIL_REVIEW.md); Peil 1.1.0 §1 (principe 1), §4.4, §9 (offline) en §11.1
  (`design/ontwerptaal/`).

## Context

De review heeft de omgezette web-app in de echte stack doorgemeten (axe, contrast in de pagina, focus, overloop en
schermafdrukken op zes breedtes, in licht en donker). Daarbij kwamen twee plekken naar voren waar de app iets anders
toonde dan Peil voorschrijft, terwijl de gegevens zelf goed waren:

1. **Zonder verbinding bleef "Gezond" groen.** Met de API gestopt toonde het overzicht "Achtergronddienst — Gezond"
   (groen vinkje). De offline-banner stond erboven, maar de badge zelf zei "gemeten en goed" over een stand die niet
   meer gemeten wordt. De Android-app deed hetzelfde in de statusregel van Start. Peil §1: "Verouderde data is nooit
   gezond"; §4.4: bij verouderd wordt de status Onbekend.
2. **Een pagina die nog nooit was opgehaald, bleef "Laden…" tonen**, ook nadat de app wist dat de server weg was. Peil
   §9: "nog nooit opgehaald = Onbekend, geen skelet"; §11.1: Onbekend + reden, niet "Laden…". Zo'n pagina had ook geen
   h1. Stap 2 en 3 hadden dit als gedragspunt laten liggen (PEIL_MIGRATIE §13 punt 5).

## Besluit

1. **Zonder verbinding wordt een laatst bekende "Gezond" weergegeven als "Verouderd"** (icoon `clock-alert`,
   gestippelde, gearceerde pil, nooit groen). Dat gebeurt op één plek: `useStatusNu()` in `StatusBadge`/`StatusIcon`
   (web) en `PeilUi.systeem(level, verouderd)` (Android). **Beperkt en Storing blijven staan** zoals ze laatst bekend
   waren: een bekend probleem mag niet verdwijnen op het moment dat de server wegvalt. Dat is de veiligste lezing van
   §4.4 ("status wordt Onbekend"): de waarschuwing blijft zichtbaar, de geruststelling verdwijnt.
2. **Kaarten tonen offline de gestippelde Verouderd-rand** (Peil §9). De aliaslaag deed dat al
   (`.from-snapshot .main .card`), maar het kaartrecept van fase 2 overschreef die rand. Nu staat de regel in de laag
   `components`; "Wat actie nodig heeft" houdt daarbij de kleur van de slechtste status.
3. **Een nooit opgehaalde pagina zonder verbinding is "Onbekend — nog niet opgehaald"** met de reden, in de vorm van
   Onbekend (`circle-help`, stippelrand). `Loading` en het nieuwe `PaginaFout` doen dat zodra de app offline is; de
   pagina haalt de gegevens gewoon op zodra de verbinding terug is (niets in een wachtrij, geen nieuw gedrag naar de
   server). Op paginaniveau staat er ook tijdens laden en fout één h1.
4. **Geen nieuwe API, geen nieuw datamodel.** Alles leest bestaande toestand (`useConnection().offline`, de
   `stale`-tijd op Android). De OpenAPI-export is byte-gelijk gecontroleerd.

## Gevolgen

- Een unittest per regel (`apps/web/src/test/peil-review.test.tsx`, `PeilUiTest`) en een uitbreiding van de
  browsertest offline (`e2e/08-offline`: geen `data-status="healthy"`, gestippelde kaart, nooit opgehaalde pagina met
  h1 en Onbekend). De Android-referentiebeelden veranderen niet (de offline-afdruk toont Beperkt, dat blijft).
- Wie offline een groene badge verwacht, ziet nu "Verouderd". Dat is de bedoeling; de offline-banner noemt de tijd van
  de laatst bekende stand.
- Niet gekozen: offline álle statussen als Onbekend tonen (zou een bekende storing verbergen), of de badge laten staan
  en alleen de kaart arceren (groen zou dan nog steeds "gemeten en goed" zeggen).
