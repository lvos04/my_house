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

my_house behoudt de bestaande koppeling met kijkrechten voor de Hub. ADR-0016 legt de verificatie van
ondertekende verzoeken en de lokale replaycontrole vast.

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
6. **Apparaatherstel (S-P3-742, bouwpunt 4, besluit 19 november 2026).** Apply vereist een duurzaam
   voorstel met betrouwbaar herstelrecept. De API leest bij het voorstel rechtstreeks terug, bewaart
   de vorige stand en bindt de diff aan de doelversie en de ETag. Apply leest opnieuw en de worker
   controleert de stand en het bestaande apparaatbeleid vlak voor verzending. Evidence meldt per
   handeling de stand, hash en tijd van elke bruikbare teruglezing.

   | Actie | Apply en herstel |
   |---|---|
   | `zone.command` | Uitvoering is beschikbaar voor alleen een doeltemperatuur op een geteste, ingeschakelde Gree-unit met actieve handmatige bediening. De adapter moet de gekoppelde identiteit, bedrijfsstand en de herstelbare Celsius-doeltemperatuur lezen. De API slaat deze stand vóór apply op. Apply en rollback gebruiken dezelfde `queue_command` en wachtrij, met de bestaande opdrachtadministratie. Readback bevestigt de doeltemperatuur, voeding en modus. Rollback zet de oude temperatuur terug en vergelijkt opnieuw met de volledige bewaarde stand. |
   | `light.group.set` | Apply en rollback geven 501. De individuele teruglezing van het groepscommando levert hier geen duurzaam herstelrecept voor alle vorige lampstanden. |
   | `light.scene.recall` | Apply en rollback geven 501. Het vorige lichtbeeld en alle toestand die een scène kan wijzigen zijn niet betrouwbaar terug te zetten via deze actie. |
   | `zone.resume` | Apply en rollback geven 501. Het schema hervatten kan later andere fysieke opdrachten veroorzaken. |
   | `zone.schedule.propose` | Het voorstel is een concept. Apply en rollback geven 501. Het actieve schema wordt niet aangepast. |

   Een onbekende vorige stand blokkeert apply. Een afwijkende teruglezing geeft `failed`. Als de
   gedeeltelijke verandering betrouwbaar leesbaar is en de context gelijk blijft, plant de worker
   meteen herstel in via dezelfde wachtrij. Onbekende of onbevestigde uitkomsten blijven zichtbaar
   in evidence. Na een onderbreking kan een nieuw ondertekend rollbackverzoek alleen herstellen als
   de actuele stand overeenkomt met de bewaarde of aangevraagde stand en er geen nieuwere opdracht
   is. Gewijzigde configuratie of een onleesbare stand blokkeert verdere bediening. De applicatie
   bewaart de actieve handmatige override en wijzigt de configuratie van het apparaat of schema niet.

## Afgewezen alternatieven

- **Hub krijgt een `control`-token** voor de bestaande routes: dan kan de Hub zonder goedkeuring/TTL/nonce schakelen
  en is "Hub kan niet schakelen" niet waar.
- **Klimaatlogica of Gree-aansturing in de Hub:** strijdig met 02_DATA_EIGENAARSCHAP.
- **Enrollment met `HUB_URL` + enrollment-token nu al verplicht maken:** my_house wordt nu handmatig geïnstalleerd;
  enrollment wordt pas nodig als de Hub my_house via de API installeert (P3, lagere prioriteit).
  *Aanvulling 2026-11-10 (contract 1.3 §2.3 regels 2 en 5, C1.3-MH-07):* ook dat vervalt: een bestaande app krijgt in
  v1 geen enrollment en `HUB_URL` start er nooit een; de Hub koppelt met een koppelcode met rol viewer en pollt. Bij
  installatie via de Hub zet de vertaling alleen `HUB_URL` (voor "Terug naar de Hub"), nooit een token of
  vingerafdruk (§3.2, RV5).

## Gevolgen

- Migratie 0006 voegt de Hub-pin, de nonces en idempotentieantwoorden toe. Migratie 0007 voegt
  voorstelbewijs toe en versieert zones en lichtdoelen. Open of onzeker apparaatherstel blokkeert
  terugval naar 0006. Bij toegestane terugval vervallen wachtende apply-opdrachten en lokale
  uitvoerbewijzen. De volledige voorstel- en herstelgeschiedenis reist in 0006 mee als instellingen.
  Bij een volgende start koppelt de applicatie deze feiten binnen één transactie aan hun oorspronkelijke
  opdracht-ID’s, ook als een eerdere versie de 0007-tabel al heeft aangemaakt. De apparaat-ID van de opdracht
  of de ETag van een voorstel koppelt eerder gemeten bedienvelden aan de juiste unit.
- Het contractfoutformaat `{code,message,details,trace_id}` geldt op de connectorroutes. De
  bestaande bedienroutes behouden `{"detail":{…}}` (ADR-0003).
- Bij uitvoerbaar klimaatwerk bindt de goedkeuring de exacte preview, ETag, actie, doel,
  parameters, risicoklasse en beleidshash. Apply en rollback verbruiken elk een eigen nonce.
- Bewijs is zichtbaar voor dezelfde aangemelde aanroeper. Een logische export bewaart vorige stand,
  opdrachtkoppelingen en herstelstatus. De lokale uitvoerrechten vervallen bij herstel.
  Voor vrijgave van een database-dump controleert de applicatie de opgeslagen koppelingen in
  een afzonderlijke PostgreSQL-database. De vorige stand moet de oorspronkelijke previewhash opleveren.
  Een vóór verzending geweigerde opdracht behoudt de reden en de destijds gemeten stand. Een ontbrekende
  koppeling of bedienstand blokkeert het aanmaken en het ontsleutelen van de dump.
- Na een onderbroken verzending toont de herstelpreview de actuele, volledig teruggelezen stand.
  Komt deze met de vorige stand overeen, dan bevestigt de worker na nieuwe goedkeuring het herstel
  met een tweede teruglezing. Bij open herstel uit een eerdere versie wachten de migratie en de nieuwe
  worker op afhandeling door de vorige installatie. Bij terugkeer naar oudere software op hetzelfde schema
  blijft een opdracht voor de huidige connector uitgesteld in de bestaande wachtrij. De bevoegde worker
  claimt de oorspronkelijke opdracht onder de woninglock en toetst opnieuw de goedkeuring en de vervaltijd.
