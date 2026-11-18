# ADR-0005 — "Geen ongeteste Gree-bediening": inbedrijfstellingsgate per apparaat

- **Status:** vastgesteld op 18 november 2026 en gebouwd in P3. Fysieke acceptatie volgt in N-P6-06.
- **Context-ID's:** B06-001, B06-002, B06-007, EX30, D12; `01_ARCHITECTUUR/06_FASERING_EN_GATES.md` (gate *my_house fysieke
  apparaten gevalideerd* vóór climate actuation)

## Context

B06-001 eist: "Geen ongeteste Gree-bediening; Hue en P1 als eerste read-only." my_house heeft twee lagen:

1. **Protocolgate** (bestaand): een Gree-unit wordt alleen gekoppeld als scan + bind + statusuitlezing slagen;
   anders `unsupported`, integratie uit, geen bediening (`gree/adapter.py:380-410`, `routers/devices.py:1100-1188`).
2. **Inbedrijfstellingsgate** (P3): `devices.compatibility` = `untested|tested|unsupported` met `tested_at`.
   Ontdekking en de leestest zijn hulpmiddelen voor de beheerder, die het resultaat expliciet bevestigt.

Vóór P3 kon de app een gekoppelde, ongeteste unit bedienen via de gebruiker, een schema of de regelaar.
Het compatibiliteitslabel maakt nu deel uit van het centrale bedienbeleid.

## Besluit

1. **Bediening bij een actieve gate** vereist de actuele status `tested` voor het doel en de betrokken
   groeps- of scèneleden. `policy.check_command` weigert ongeteste bediening met `409 device_not_tested`
   en verwijst naar testen en bevestigen onder **Apparaten** in de web-app. Veilige modus, boilerbescherming
   en blokkade bij een onbruikbaar protocol hebben voorrang. `permit_join` op een actieve Zigbee-bridge
   is een beheerhandeling waarop de teststatuscheck geen blokkade zet.
2. **Uitrolbesluit van 18 november 2026.** Migratie 0005 begint met de gate aan als er geen apparaatrijen
   zijn. Als er al apparaten zijn, inclusief gearchiveerde, begint de gate uit. Een eerder vastgelegde
   gatetoestand blijft behouden bij migratie omlaag en weer omhoog. Bij een onbekende toestand past
   de server de beveiliging toe. De migratie voegt ook de optionele velden `detection_method`
   (`scan` of `manual`) en `fallback` toe.
3. **Beheerbevestiging.** Ontdekking, koppeling en de leestest zetten een apparaat nooit automatisch op
   `tested`. Een beheerder beoordeelt de leestest en kan de laatste bevestigde bediening als aanwijzing
   gebruiken. De beheerder bevestigt een apparaat of een expliciet gekozen selectie. Het apparaatscherm
   toont de bevestigingshistorie uit `device_marked_tested`. De beheerder schakelt de gate bewust in na
   inzage in de gevolgen voor alle woningen.
4. **Handhaving.** De API controleert vóór het plaatsen van de bedienopdracht. De worker herleest vóór
   verzending de actuele gate, doelen en teststatus, ook voor eerder opgeslagen opdrachten en retries.
   De controle geldt ook voor knopkoppelingen, schema’s en klimaatregeling. Wanneer de gate klimaatsturing
   tegenhoudt, regelt de unit zelf en toont de zone de reden.
5. **Wizardvolgorde.** Eerst **Meters uitlezen**, daarna **Hue uitlezen** en **Hue testen en bevestigen**.
   De Hue-leestest schakelt niets. Voor de te bevestigen lamp is een eigen geslaagde leestest nodig.
   De wizard toont voor Gree de wachtstand voor D12 en biedt **Gree later instellen**. De fysieke koppel-
   en terugleesproef blijft vereist. Bij een protocolfout tijdens binden blijft EX30 van toepassing
   (`unsupported`). Gebruik voor Gree zolang nodig de eigen afstandsbediening.

## Afgewezen alternatief

- **Protocolgate als "getest" beschouwen:** dan bestaat er geen menselijke validatie op echte hardware vóór bediening,
  strijdig met de gate in `06_FASERING_EN_GATES.md`.

## Gevolgen

- Bij de eerste opwaardering naar 0005 met bestaande apparaten en zonder eerdere gatetoestand blijft
  de bestaande bediening beschikbaar onder het overige veiligheidsbeleid. Zodra de beheerder de gate
  inschakelt, stopt my_house de bediening van ongeteste doelen.
- De P3-proeven dekken de API-weigering, worker-hercontrole, bediening tot aan de testadapter,
  bevestiging en audit, leestest en migratiebehoud. De toekomstige connector moet in S-P3-742
  hetzelfde commandopad gebruiken en de weigering afzonderlijk toetsen.
- De [API-handleiding](../api.md#inbedrijfstellingsbeheer) beschrijft de beheerroutes. De
  [compatibiliteitsmatrix](../compatibility.md#detectie-protocol-en-terugval) noemt de open API-velden
  onder P3-07. De [deltamatrix](../platform-v2/DELTA_MATRIX.md#stand-op-18-november-2026-s-p3-710)
  koppelt het softwarebewijs aan de open fysieke acceptatie.
