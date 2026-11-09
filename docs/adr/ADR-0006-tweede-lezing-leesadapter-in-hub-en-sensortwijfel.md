# ADR-0006 — Tweede lezing: leesadapter in de Hub, bewaakt schrijfpad in my_house, sensortwijfel

- **Status:** voorgesteld (tweede lezing van de P0-delta-matrix). Punt 1 heeft bevestiging nodig van de eigenaar van de
  Hub-adapter (`ai-server-manager`) vóór P3 begint.
- **Context-ID's:** B06-004, B06-005, B06-006, B06-007, A20-A1, EX19, EX22; `PLATFORM_CONTRACTEN.md` §5, §6, §8;
  `01_ARCHITECTUUR/03_RISICO_AUTONOMIE.md`; `DELTA_MATRIX.md` §11.
- **Herziet:** ADR-0002 punt 2 (opgeschort). ADR-0002 punten 1 en 3–6 blijven staan.

## Context

De tweede lezing van de delta-matrix (`DELTA_MATRIX.md` §11) liet zien dat de Hub alles wat hij moet tonen al kan lezen
via **bestaande** routes met een koppelcode met rol `viewer`: woning, zones, licht, energie (met kwaliteit), meldingen,
apparaten, status en deep-link. Een viewer-token kan niets bedienen (403; Sim-getest), en de rol van een gekoppelde
client is achteraf niet te verhogen.

Contract §6 zegt voor bestaande apps: *"krijgen in de Hub een adapter die hun **bestaande** routes gebruikt; nieuwe
routes in die apps alleen als hun delta-matrix ze als ontbrekend markeert."* ADR-0002 punt 2 plande daarnaast een eigen
leeslaag `GET /connector/v1/discover|health|items|events` in my_house. Dan bouwen twee teams dezelfde vertaling (de Hub
een adapter, my_house een connector): dubbel werk en extra aanvalsoppervlak.

Daarnaast botsen twee teksten licht:
- `03_RISICO_AUTONOMIE.md`: *"Smart Home: … bij twijfel sensor-/adapterstatus `unknown` en geen aansturing."*
- B06-004: *"stale Zigbee stopt optimalisatie"* — my_house valt dan terug op de eigen sensor van de unit (OEM) en laat het
  schema doorlopen, zonder bijsturing.

## Besluit

1. **Lezen door de Hub = Hub-adapter op bestaande routes** (veiligste lezing van contract §6: zo min mogelijk nieuw
   oppervlak in my_house). my_house bouwt in P3 alleen wat de Hub van buitenaf **niet** kan:
   - het **bewaakte schrijfpad** `POST /connector/v1/propose`, `/apply`, `GET /connector/v1/evidence/{id}`,
     `POST /connector/v1/rollback` (→ 501), volgens ADR-0002 punten 3–6. Dat hergebruikt de bestaande commando-TTL
     (`worker/commands.py:39-40`), de zone-capabilities als parameterschema en `request_zone_command`/`queue_command`.
     Een versiekolom voor de ETag is nieuw (zones en apparaten hebben geen `updated_at`);
   - `GET /healthz` en `GET /readyz` als healthcheck van de alles-in-één container (P3-01), afgeleid van de bestaande
     `/api/v1/health` en `views.system_checks`.

   **Niet** in my_house, tenzij de eigenaar van de Hub-adapter erom vraagt: `discover`, `health`, `items`, de outbox
   en `events?cursor=` en `api_clients.kind`. Meldingen en status zijn te pollen; de Hub markeert zelf stale data.
2. **Sensortwijfel versus adaptertwijfel.**
   - Twijfel over de **unit/adapter** (offline, stand onbekend, net herstart): **geen enkel commando**. Dat bestaat al
     (503 `device_offline`; na herstart niets tot de stand is uitgelezen).
   - Twijfel over de **externe sensor** (verouderd of onwaarschijnlijk): **geen bijsturing op basis van die sensor**.
     De unit regelt op zijn eigen sensor (OEM) en het schema mag modus en doel blijven zetten. Dat bestaat al
     (`oem_fallback`, correctie 0). Volledig stoppen zou een unit in de laatste stand laten doordraaien, bijvoorbeeld
     verwarmen buiten het schema. Dat is minder veilig. De twijfelachtige waarde wordt in beide gevallen nooit gebruikt.

## Afgewezen alternatieven

- **Volledige connector-leeslaag in my_house (ADR-0002 punt 2):** dubbel met de Hub-adapter uit contract §6; geen eis
  van B06 die alleen zo te halen is.
- **Hub een `control`-token geven in plaats van het schrijfpad te bouwen:** dan is "Hub kan niet schakelen" niet meer
  waar en vervallen TTL/nonce/handtekening (zie ADR-0002).
- **Bij sensortwijfel alle aansturing stoppen:** laat units buiten het schema draaien; strijdig met B06-004 ("stopt
  optimalisatie", niet "stopt alles").

## Gevolgen

- Bouwlijst: zie `DELTA_MATRIX.md` §11.6. P3-03 vervalt in my_house, P3-04 wordt P2/optioneel en P3-05 blijft P0.
- Contract_issue gemeld: de eigenaar van de Hub-adapter bevestigt punt 1. Vraagt die toch om een leeslaag in
  my_house, dan geldt ADR-0002 punt 2 weer, als dunne laag boven de bestaande `views/*`.
- Punt 2 vraagt geen codewijziging. P3-08 voegt wel de ontbrekende test voor de sensormelding (`sensor_round`) toe.
