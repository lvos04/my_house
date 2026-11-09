# ADR-0002 — Hub-koppeling: lezen via bestaande routes, bedienen alleen via een ondertekend verzoek

- **Status:** voorgesteld (ontwerp in P0, uitvoering in P3). **Punt 2 opgeschort door ADR-0006** (tweede lezing:
  de leesadapter hoort volgens contract §6 in de Hub); punten 1 en 3–6 blijven.
- **Context-ID's:** B06-003, B06-006, B06-007, EX16, EX17, EX18, EX20, EX27, EX45; `PLATFORM_CONTRACTEN.md` §3, §4, §6–§8;
  `01_ARCHITECTUUR/02_DATA_EIGENAARSCHAP.md`

## Context

my_house is eigenaar van woning, klimaat, energie en apparaten; de Hub krijgt "read-only telemetrie en guarded command
delegation" en mag geen eigen klimaatregelaar bouwen. B06-006: "my_house offline → oude data met stale mark; Hub kan
niet schakelen". B06-003: elke poging om de boiler te schakelen, ook vanuit Hub/AI, geeft `policy_denied`.

my_house heeft al:
- een koppelcode-mechanisme (`POST /pairing/codes` → `POST /pairing/redeem`) dat een bearer-token met een eigen rol
  (`admin|control|viewer`) uitgeeft, verloopt na 180 dagen, vernieuwbaar en intrekbaar (401 `token_revoked`);
- alle leesroutes die een huiskaart nodig heeft (overzicht, zones, licht, energie, meldingen, apparaten, status) op
  toegangsniveau `auth`, en een WebSocket die ook met een token werkt;
- één commandoroute (`commanding.request_zone_command` / `queue_command`) met het volledige veiligheidsbeleid.

Er is geen machine-identiteit, geen connectorcontract, geen idempotency, geen verificatie van een Hub-handtekening.

## Besluit

1. **Lezen (P3, minimaal):** de Hub koppelt via een koppelcode die de gebruiker in de my_house-web-app maakt met rol
   **`viewer`** en in de Hub-UI invoert (contract §3 "handmatig geïnstalleerde apps koppelen via een koppelcode"). De
   Hub wisselt de code in via de bestaande `POST /api/v1/pairing/redeem` en bewaart het token in zijn vault. Een
   `viewer`-token kan op de bestaande routes **niets** bedienen (403 `forbidden`). Om verwarring met telefoons te
   voorkomen krijgt `api_clients` een soort (`kind: phone|service`, migratie 0005); een `service`-client kan nooit
   een hogere rol dan `viewer` krijgen op de gewone routes.
2. **Connectorcontract (P3):** nieuwe module `connector/` met `GET /connector/v1/discover|health|items|events|evidence/{id}`
   als **lees-modellen boven de bestaande views** (`views/zones.py`, `views/energy.py`, `views/status.py`) — geen
   tweede berekening. `/healthz` en `/readyz` worden afgeleid uit de bestaande health/status-checks. Events komen uit
   een outbox-tabel (migratie 0005) die gevuld wordt waar nu al `events.publish` en `raise_alert` gebeuren.
3. **Bedienen (P3):** alleen via `POST /connector/v1/propose` (preview, `preview_hash`, `resource_etag`) en
   `POST /connector/v1/apply` met een door de Hub **ondertekend `ExecutionRequest`** (Ed25519, publieke policy-sleutel
   uit de koppeling). my_house controleert zelf: handtekening, vervaltijd, eenmalige nonce (tabel), `Idempotency-Key`,
   `action_id` in een eigen allowlist, parameterschema, ETag-preconditie — en roept daarna **dezelfde**
   `request_zone_command` / `queue_command` aan, zodat `policy.check_command`, de buitenunit-lock, de veilige modus en
   "kies eerst een modus" ongewijzigd gelden. Het resultaat is `202` + `command_id`; "uitgevoerd" pas na `confirmed`
   (readback) via `GET /connector/v1/evidence/{command_id}`.
4. **Allowlist v1:** `light.group.set`, `light.scene.recall`, `zone.command`, `zone.resume`,
   `zone.schedule.propose` (alleen een concept-schema, geen directe toepassing). **Nooit** in de allowlist: alles wat
   een energiemeter/stekker schakelt (`set_relay`, `switch`, …), bewerkingen op rollen/apparaten/gebruikers,
   back-up/herstel, veilige modus opheffen. De risicoklasse bepaalt de Hub-policy (R1 licht; R2 klimaat); my_house
   negeert `risk_claim`.
5. **Boiler:** elk propose/apply-verzoek waarvan het doel een apparaat met beschermde rol is, of een meter, geeft
   **altijd** `409 {code:"policy_denied"}` — ook met geldige handtekening en goedkeuring. Bestaande routes die een
   beschermd apparaat als doel krijgen, antwoorden voortaan ook `409 policy_denied` in plaats van 404/422.
6. **Rollback:** `POST /connector/v1/rollback` → `501 {code:"capability_unavailable"}` (een fysieke
   klimaatwijziging is niet betrouwbaar terug te draaien; *Terug naar schema* is een eigen actie `zone.resume`).

## Afgewezen alternatieven

- **Hub krijgt een `control`-token** voor de bestaande routes: dan kan de Hub zonder goedkeuring/TTL/nonce schakelen
  en is "Hub kan niet schakelen" niet waar.
- **Klimaatlogica of Gree-aansturing in de Hub:** strijdig met 02_DATA_EIGENAARSCHAP.
- **Enrollment met `HUB_URL` + enrollment-token nu al verplicht maken:** my_house wordt nu handmatig geïnstalleerd;
  enrollment wordt pas nodig als de Hub my_house via de API installeert (P3, lagere prioriteit).

## Gevolgen

- Nieuwe tabellen (migratie 0005, met downgrade): `connector_outbox`, `connector_nonces`, `idempotency_keys`,
  kolom `api_clients.kind`.
- Contractfoutformaat `{code,message,details,trace_id}` geldt alleen op de nieuwe connector- en health-routes; de
  bestaande routes houden `{"detail":{…}}` (web en Android-app hangen ervan af) — zie ADR-0003.
