# ADR-0005 — "Geen ongeteste Gree-bediening": inbedrijfstellingsgate per apparaat

- **Status:** voorgesteld (interpretatie in P0, bouwen in P3)
- **Context-ID's:** B06-001, B06-002, B06-007, EX30, D12; `01_ARCHITECTUUR/06_FASERING_EN_GATES.md` (gate *my_house fysieke
  apparaten gevalideerd* vóór climate actuation)

## Context

B06-001 eist: "Geen ongeteste Gree-bediening; Hue en P1 als eerste read-only." my_house heeft twee lagen:

1. **Protocolgate** (bestaand): een Gree-unit wordt alleen gekoppeld als scan + bind + statusuitlezing slagen;
   anders `unsupported`, integratie uit, geen bediening (`gree/adapter.py:380-410`, `routers/devices.py:1100-1188`).
2. **Compatibiliteitslabel** (bestaand): `devices.compatibility` = `untested|tested|unsupported` met `tested_at`;
   `POST /devices/{id}/test` leest alleen, `POST /devices/{id}/tested` zet het label (beheerder).

Het label wordt nu **niet** gebruikt bij bediening: een gekoppelde maar `untested` unit kan meteen door gebruiker,
schema en regelaar worden bediend. Het pakket laat open of "ongetest" de protocolgate of de menselijke bevestiging
bedoelt.

## Besluit (veiligste interpretatie)

1. **Bediening (`set_hvac`, licht-commando's) alleen voor apparaten met `compatibility == "tested"`.** Anders weigert
   `policy.check_command` met `409 policy_denied` en de uitleg *"Dit apparaat is nog niet getest. Doe eerst de leestest
   en markeer het als getest op Apparaten."* Dit komt in de bestaande beleidsfunctie (API én worker), niet in een
   nieuwe laag.
2. **Volgorde van inbedrijfstelling** (installatiewizard + I05): eerst HomeWizard P1/meters (alleen lezen, kunnen
   nooit schakelen), dan Hue **alleen lezen** tot de beheerder één lamp met readback heeft getest en *getest* kiest,
   dan Gree pas na D12 (echte bind/readback op het eigen model/firmware) en één bevestigde setpointwijziging.
3. **Gevolg voor de regelaar:** een `untested` unit valt onder *OEM regelt zelf* (zoals bij een verouderde sensor);
   de zone toont de reden. Er wordt nooit iets aangezet om te testen; de leestest schakelt niets.
4. **Bestaande installaties:** bij de update krijgen apparaten met minstens één bevestigd (`confirmed`) commando een
   eenmalige melding *"Bevestig dat dit apparaat werkt"*; ze worden **niet** automatisch op `tested` gezet (dat zou
   een niet-uitgevoerde menselijke test claimen). Tot de bevestiging valt bediening terug op de afstandsbediening.
5. EX30 blijft: protocolfout bij binden → `unsupported`, nooit pseudo-bediening.

## Afgewezen alternatief

- **Protocolgate als "getest" beschouwen:** dan bestaat er geen menselijke validatie op echte hardware vóór bediening,
  strijdig met de gate in `06_FASERING_EN_GATES.md`.

## Gevolgen

- Gedragswijziging voor wie 0.2.0 al gebruikt (klimaatbediening pauzeert tot bevestiging). Dit wordt in de
  release-notes en in de app uitgelegd; de eigenaar kan dit besluit in P3 nog aanpassen (bijv. alleen voor Gree).
- Tests in P3: `untested` → 409 `policy_denied` op API, worker en connector; `tested` → bediening; leestest schakelt
  niets.
