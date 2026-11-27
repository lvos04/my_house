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
   `zone.schedule.propose` (conceptschema) en `socket.relay.propose` (concept voor een stekkerstand, EX27). **Nooit** in de allowlist: alles wat
   een energiemeter/stekker schakelt (`set_relay`, `switch`, …), bewerkingen op rollen/apparaten/gebruikers,
   back-up/herstel, veilige modus opheffen. De risicoklasse bepaalt de Hub-policy (R1 licht; R2 klimaat); my_house
   negeert `risk_claim`.
5. **Boiler.** Een beschermde rol blokkeert ieder voorstel en ieder bedienverzoek met
   `409 policy_denied`, ook met een geldige handtekening en goedkeuring. Voor meters geldt dezelfde blokkade.
   Alleen een actieve, geteste HomeWizard Energy Socket zonder beschermde rol kan een ondertekend
   `socket.relay.propose` met één booleaanse stand als concept krijgen. Bij herhaling van het voorstel
   controleert de connector de actuele rol opnieuw. Bestaande routes die een
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
   | `socket.relay.propose` | Een concept bevat de gewenste stand en het apparaat-ID. Apply en rollback geven 501 voor de gewone stekker en 409 zodra de stekker beschermd is. Er komt geen relaisopdracht in de wachtrij. |

   Een onbekende vorige stand blokkeert apply. Een afwijkende teruglezing geeft `failed`. Als de
   gedeeltelijke verandering betrouwbaar leesbaar is en de context gelijk blijft, plant de worker
   meteen herstel in via dezelfde wachtrij. Onbekende of onbevestigde uitkomsten blijven zichtbaar
   in evidence. Na een onderbreking kan een nieuw ondertekend rollbackverzoek alleen herstellen als
   de actuele stand overeenkomt met de bewaarde of aangevraagde stand en er geen nieuwere opdracht
   is. Gewijzigde configuratie of een onleesbare stand blokkeert verdere bediening. De applicatie
   bewaart de actieve handmatige override en wijzigt de configuratie van het apparaat of schema niet.

   Een temperatuurvoorstel van de Hub vereist een lokale beheerder die het oorspronkelijke voorstel
   kan herstellen. Dat is de webbeheerder die de koppeling heeft gemaakt. Zonder die bevoegdheid
   geven voorstel en uitvoering HTTP 501 `capability_unavailable` met reden `rollback_unavailable`.
   De worker controleert de bevoegdheid opnieuw vóór verzending van het nieuwe setpoint.

   Voor expliciet herstel opent de beheerder in de lokale API
   `GET /api/v1/system/hub-recovery/{proposal_id}` met het bewijs-ID uit het Hub-journaal.
   Het antwoord toont de zone, de metingen en de herstelpreview. Een `POST` op hetzelfde adres
   bevestigt de `preview_hash` van die herstelpreview met het eigen wachtwoord (`password`),
   de CSRF-header van de websessie en een nieuwe `Idempotency-Key`. Bij herhalen hoort dezelfde
   sleutel. De worker leest de volledige vorige stand terug en legt het resultaat vast bij
   hetzelfde bewijs-ID. De Hub-koppeling houdt alleen de schrijfroutes `propose` en `apply`.

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
  voorstelbewijs toe en versieert zones en lichtdoelen. Migratie 0009 versieert ook de plaatsing
  van de unit. Eerder geclaimd werk met een onzekere uitkomst blokkeert de terugval. Een ongeclaimde
  apply of rollback wordt bij een wissel geannuleerd. De volledige voorstel- en herstelgeschiedenis reist in 0006 mee als instellingen.
  Bij een volgende start koppelt de applicatie deze feiten binnen één transactie aan hun oorspronkelijke
  opdracht-ID’s, ook als een eerdere versie de 0007-tabel al heeft aangemaakt. De apparaat-ID van de opdracht
  of de geverifieerde meetidentiteit van een voorstel koppelt eerder gemeten bedienvelden aan de juiste unit.
- Het contractfoutformaat `{code,message,details,trace_id}` geldt op de connectorroutes. De
  bestaande bedienroutes behouden `{"detail":{…}}` (ADR-0003).
- Bij uitvoerbaar klimaatwerk bindt de goedkeuring de exacte preview, ETag, actie, doel,
  parameters, risicoklasse en beleidshash. Apply en rollback verbruiken elk een eigen nonce.
- Connectorbewijs is zichtbaar voor dezelfde aangemelde aanroeper. De lokale herstelroute geeft de
  webbeheerder ook toegang tot het setpointbewijs van de eigen Hub-koppeling. Een logische export bewaart vorige stand,
  opdrachtkoppelingen en herstelstatus. De lokale uitvoerrechten vervallen bij herstel.
  Voor vrijgave van een database-dump controleert de applicatie de opgeslagen koppelingen in
  een afzonderlijke PostgreSQL-database. De vorige stand moet de oorspronkelijke previewhash opleveren.
  Een vóór verzending geweigerde opdracht behoudt de reden en de destijds gemeten stand. Een ontbrekende
  koppeling of bedienstand blokkeert het aanmaken en het ontsleutelen van de dump.
- Na een onderbroken verzending toont de herstelpreview de actuele, volledig teruggelezen stand.
  Komt deze met de vorige stand overeen, dan bevestigt de worker na nieuwe goedkeuring het herstel
  met een tweede teruglezing.

## Toestandstabel bij versiewissels

Besluit van 20 november 2026. Onder de installatielock vervalt iedere ongeclaimde connectoropdracht
bij de overgang naar andere software of een ander schema. De wachtrij bewaart de opdracht-ID,
nul claims en de reden `versiewissel`. De oorspronkelijke preview en meetvelden blijven beschikbaar.
Een geannuleerde rollback opent voor de beheerder de melding **Handmatige controle nodig**, met
de rollback-ID. Een start op dezelfde versie sluit eveneens wachtend werk af. Een tussentijdse
start van oudere software in de uitsluitend-lezen herstelmodus laat immers geen versiespoor achter.

De import van een gecontroleerde SQL-terugvalkopie sluit de wachtrij binnen de importtransactie.
Bij iedere terugval voert de huidige versie `shc-api prepare-connector-fallback` uit op de
[offline kopie](../onderhoud.md#offline-terugvalkopie-voorbereiden), vóór de oude worker toegang krijgt. De CLI koppelt een nieuw Terugval-ID, de gecontroleerde revisie en de bestandsidentiteit van de gekoppelde PostgreSQL-opslag in dezelfde
transactie aan de database. Het alleen-lezen startbestand uit de huidige release leest bij elke start de
catalogus van het aangesloten PostgreSQL en controleert of de connectorwachtrij veilig is. De historische
migratie, API en worker komen pas na die controle aan bod. Een mislukte nieuwe voorbereiding trekt een
eerder ID in. De fysieke proeven starten het historische workerproces ook zonder ID en met een verwisselde kopie.
Geclaimd werk met een onzekere uitkomst
houdt de terugval tegen. Een mislukte controle draait de wijzigingen in de kopie terug.

De meetidentiteit bindt unit, zone, buitengroep en configuratie aan de preview. Een verandering
van plaatsing verhoogt de apparaatversie, ook bij terugplaatsing. Iedere nieuwe preview gebruikt
een directe teruglezing. Een oude meting met onzekere herkomst levert alleen de namen op van
velden die bij mogelijke units opnieuw gelezen moeten worden. Bij het drieveldformaat ontbreekt
deze veldinventaris. Daarvoor is een volledige bedienmeting nodig.

De historische proeven gebruiken echte programmaversies uit Git en een lokale klimaatsimulator.
7a is `299cb5b`, 7b is `44486bf`. Op schema 0008 zijn onder andere `000a036`, `5079599`
en `140d39d` beproefd. Huidig verwijst naar schema 0009.

| Toestand | Software en schema | Gecontroleerd resultaat | Regressieproef |
|---|---|---|---|
| Alleen voorgesteld, ongewijzigde koppeling | 7b/0007 → huidig/0009 | De ETag bevestigt de unit. Een later ontbrekende stille stand blokkeert apply. | `test_oude_stille_stand_blijft_bekend_bij_versiewissel` |
| Alleen voorgesteld, buitengroep gewijzigd vóór identificatie | 7b/0007 → huidig/0009 | De historische stand blijft bewaard. Apply geeft 409 met nul opdrachten. | `test_oude_meting_na_wijziging_buitengroep` |
| Oude meting van voeding, modus en doeltemperatuur, zonder bedienvelden | 7a/0007 → huidig/0009 na groepswijziging | Zonder historische veldinventaris of volledige nieuwe bedienmeting blijft de bediening dicht. Apply geeft 409 met nul opdrachten. | `test_oude_drieveldmeting_na_groepswissel` |
| Nieuwe meting na groepswijziging | Huidig/0009 → herstart/0009 | Beide metingen houden hun eigen groep en dezelfde unit. Een verdwenen veld blokkeert de opdracht. | `test_meetidentiteit_bij_nieuwe_groep` |
| Dezelfde unit verhuist naar een andere zone | 7b/0007 → huidig | Een teruglezing zonder de historische stille stand geeft 409 en nul connectoropdrachten. Een volledige nieuwe meting maakt een nieuw voorstel uitvoerbaar. | `test_dezelfde_unit_in_andere_zone` |
| Buitengroep wijzigt en krijgt de oude naam terug | Huidig | De eerdere preview geeft 409 bij beide wijzigingen. Een nieuwe meting krijgt een andere ETag. | `test_plaatswissel_vergt_nieuwe_meting` (buitengroep) |
| Unit losgemaakt en teruggeplaatst | Huidig | De oude preview geeft 409 en nul opdrachten, ook na terugplaatsing. Een nieuwe meting herstelt de bediening. | `test_plaatswissel_vergt_nieuwe_meting` (unit_opnieuw_geplaatst) |
| Unit in een andere kamer en weer terug | Huidig | Beide wijzigingen maken de eerdere preview ongeldig. Een nieuwe meting maakt een nieuw voorstel uitvoerbaar. | `test_plaatswissel_vergt_nieuwe_meting` (kamer) |
| Apply ingepland | Huidig/0009 → 0006 → 7b/0007 → huidig/0009, tweemaal | De opdracht vervalt, de bewijsrij blijft behouden en de oude workers verzenden niets. | `test_voorstel_en_wachtende_apply_na_twee_schemawissels` |
| Apply geclaimd, verzending onderbroken | Huidig/0009 → herstart/0009 | De vastgelegde claim en verse teruglezing bepalen welke herstelactie beschikbaar is. | `test_onderbroken_claim_sluit_met_verse_teruglezing` |
| Apply verzonden | Huidig/0009 → dump en herstel/0009 | Claim en oorspronkelijke stand keren terug uit de dump. De simulator bereikt na goedkeuring 20 °C. | `test_onderbreking_na_echte_simulatorverzending` (apply) |
| Apply bevestigd, rollback ongepland | 7a of 7b/0007 → huidig/0009 | Open herstel houdt de upgrade tegen. Na herstel in de oude installatie kan de upgrade door. | `test_apply_bevestigd_zonder_geplande_rollback` |
| Rollback ingepland op ouder schema | 7a en 7b/0007 → huidig | Nul claims, oorspronkelijke herstel-ID, versiewissel als reden en open herstelmelding. | `test_vorige_installatie_ziet_geannuleerde_rollback` |
| Rollback ingepland, twee schematerugvallen | Huidig → 0008 → huidig, tweemaal | Elke worker laat de geannuleerde ID op nul claims. De unit blijft op 22 °C. | `test_terug_naar_vorige_worker_met_open_rollback` |
| Terugval enkel in leesmodus | Huidig → vorige software op 0009 → huidig | De oude worker wacht. Na herstart toont evidence de annulering. | `test_alleen_lezen_terugval_en_opnieuw_vooruit` |
| Automatische SQL-terugvalkopie | 0008 → huidig → aparte kopie op 0008 | De import bewaart de rollback als historie en maakt de melding zichtbaar voor de oude applicatie. | `test_versiewissel_annuleert_ook_op_terugvalkopie` (automatisch) |
| Handmatige export uit voorgaande software | 0008 → huidig → aparte kopie op 0008 | De gecontroleerde SQL sluit de opdracht af voordat de oude worker start. | `test_versiewissel_annuleert_ook_op_terugvalkopie` (handmatig) |
| Offline kopie met oorspronkelijke wachtrij | 0008 → voorbereide kopie op 0008 → huidig | Voorbereiding annuleert op het oude schema. ID, preview en melding blijven bewaard. | `test_versiewissel_annuleert_ook_op_terugvalkopie` (opslagkopie) |
| Installatieformulier en onderhoud met fysieke snapshot | 0008 → 0009 op bron, daarna voorbereide kopie op 0008 | De eigenlijke vorige worker houdt nul claims en verzendingen bij apply en rollback. Opdracht-ID, preview en meetvelden blijven gelijk. Alleen de rollback geeft een beheermelding. | `test_snapshot_met_bijbehorende_vorige_software` |
| Volledige logische export met een wachtende rollback | 0008 → huidig schema, of gelijk schema | Bij import uit het oude archief blijft de ontbrekende teruglezing SQL NULL. Een ander schema, versienummer of softwarebouw annuleert de wachtende rollback met reden `versiewissel` en een beheermelding. Bij dezelfde bouw geldt de gewone herstelreden. In alle gevallen blijven claims en extra verzendingen op nul. | `test_versiewissel_met_logische_export` (R-HARDE-EIS-16, S-P3-730) |
| Rollback binnen dezelfde lopende installatie | Vorige/0008 | De eigen worker herstelt na goedkeuring de gemeten 20 °C. | `test_zelfde_schema_rollback_in_voorgaande_software` |
| Beschadigde terugvalexport | Softwarewissel op 0009 | De mislukte SHA-256-controle draait de annulering terug. | `test_softwarewissel_op_hetzelfde_schema_en_opnieuw_vooruit` |
| Bestaande reservering een uur vooruit | 0008 → huidig, daarna eerdere en huidige workers | De opdracht houdt nul claims. De meldingenlijst bevat de oorspronkelijke rollback-ID. | `test_toekomstige_reservering_na_softwarewissel` |
| Oude worker draait nog bij een opdracht van de nieuwe software | Worker uit 0008 op huidig/0009 | De database weigert zijn claim met nul verzendingen. De huidige worker claimt de nieuwe opdracht precies één keer. | `test_reeds_actieve_worker_mag_nieuwe_opdracht_niet_claimen` |
| Software wisselt heen en terug bij gelijk schema | 0009 → 0009 → 0009 | Bij beide wissels vervalt de open rollback. | `test_softwarewissel_op_hetzelfde_schema_en_opnieuw_vooruit` |
| Rollback verzonden | Huidig/0009 → dump en herstel/0009 | De dump bewaart de claim. Een afwijkende bedienstand houdt herstel open. | `test_onderbreking_na_echte_simulatorverzending` (rollback), `test_teruglezing_na_geweigerde_downgrade` |
| Rollback bevestigd | Huidig/0009 → 0006 → 7b/0007 → huidig/0009, tweemaal | Bewijs en beide opdracht-ID’s keren terug zonder lokaal uitvoerrecht. | `test_oude_0007_leest_bewaard_bewijs_na_schemawissel`, `test_logische_export_op_0006_neemt_hersteld_bewijs_mee` |
| Onderbroken vóór verzending | Huidig/0009 → herstart/0009 → 0006 | Verse teruglezingen bevestigen de oorspronkelijke stand zonder verzending. | `test_onderbreking_voor_verzending` |
| Historie na weigering vóór verzending | 7a/0007 → huidig/0009 | Beide geteste dumpversies behouden opdracht, reden en stand. | `test_oude_vervallen_opdracht_laat_nieuwe_backups_beschikbaar` |
| Historie met gelijke voor- en nastand | 7b/0007 → huidig/0009 → vorige/0008 → huidig/0009 | Afgehandelde historie laat upgrade en herstart toe. | `test_oude_afgehandelde_opdracht_houdt_upgrade_vrij` |
| Historie na schema-annulering, later een nieuw veld | Huidig/0009 → 0006 → huidig/0009 | Dump en herstart bewaren de oorspronkelijke preview met nul claims en vervallen recht. | `test_onuitgevoerde_historie_na_nieuw_bedienveld` |
| Geannuleerde drieveldpreview, later stille stand gelezen | 7a/0007 → 0008 → 0006 → 7b/0007 → huidig | Dump en herstart bewaren ID, previewhash en oude meting bij nul claims en nul rechten. | `test_schema_annulering_met_oorspronkelijke_preview` (7a) |
| Geannuleerde preview met bedienvelden | 7b/0007 → 0008 → 0006 → 7b/0007 → huidig | Dump en herstart behouden alle destijds gemeten velden. | `test_schema_annulering_met_oorspronkelijke_preview` (7b) |
| Geannuleerde opdracht met lokaal recht | Huidig/0009 na schematerugval | De broncontrole en de herstart weigeren de als vervallen aangemerkte opdracht. | `test_onuitgevoerde_historie_na_nieuw_bedienveld` |
| Geannuleerde opdracht met een claim | Huidig/0009 na schematerugval | De dump weigert de afwijkende claimteller. | `test_onuitgevoerde_historie_na_nieuw_bedienveld` |
| Beschadigde vorige stand | Huidig/0009 na schematerugval en met ingeplande opdracht | Ook voor oude historie blokkeert een afwijkende previewhash de dump en de herstart. | `test_onuitgevoerde_historie_na_nieuw_bedienveld`, `test_dump_weigert_verlies_van_een_vastgesteld_veld` |
| Meetidentiteit uit de preview ontbreekt | Huidig/0009 → herstart/0009 | De verwijzing in de bewijsrij wijst de ontbrekende identiteit aan. Dump en herstart worden geweigerd. | `test_meetidentiteit_bij_nieuwe_groep` |
| Verweesde historie | Huidig/0009 en historische export uit 7a/0007 | Een connectoropdracht zonder bijbehorende bewijsrij blokkeert de dump. | `test_geannuleerde_opdracht_zonder_bewijs_blijft_verweesd`, `test_basis_eerder_teruggezette_opdracht_in_nieuwe_dump` |
| Gevulde installatie | 0006 → huidig/0009 → aparte kopie op 0006 | Inhoud van tabellen en sequenties wordt met de voorstaat vergeleken. | `test_gevulde_upgrade_en_sha_terugweg` |

### Gepubliceerde terugvalroutes

De historische kopieproef draait met PostgreSQL 16 en de testomgeving van de API. Neem
`P=apps/api/tests/connector_versies` vanaf de repowortel en draai
`bash "$P/kopieproef" onafhankelijk direct_vorig "$P/opslagkopie.py"`. De proef vergelijkt
de actieve database met een afzonderlijk, fysiek gekopieerd en met SHA-256 gecontroleerd cluster
voor elk van de vier combinaties.

| Ingang | Voorwaarde vóór de oude worker start | Regressietest of documentcontrole |
|---|---|---|
| [Installatiehandleiding, Updaten en terugdraaien](../installatie-truenas.md#updaten-en-terugdraaien). [README](../../README.md) en [release-notities](../release-notes.md) verwijzen naar deze route. | Eenmalig startprogramma van de huidige Custom App op de schrijfbare snapshotkloon. Het vaste startbestand weigert de oude tag zonder het Terugval-ID van de aangekoppelde kloon. | `test_snapshot_met_bijbehorende_vorige_software` (installatie, apply en rollback), `test_offline_start_gebruikt_alleen_postgres_en_de_huidige_voorbereiding`, `infra/tests/allinone_container.py` (de gebouwde image, bestaand en onbekend schema) |
| Onderhoud, [Updates en terugdraaien](../onderhoud.md#updates-en-terugdraaien) en [Back-ups → Offline terugvalkopie voorbereiden](../onderhoud.md#offline-terugvalkopie-voorbereiden). | De huidige CLI werkt op de kopie. Het gekoppelde startbestand weigert een ontbrekend, ingetrokken of verwisseld ID. | `test_snapshot_met_bijbehorende_vorige_software` (onderhoud, apply en rollback), `test_terugvalvoorbereiding_weigert_onbekende_revisie`, `test_terugval_id_vereist_dezelfde_kopie_en_laatste_geslaagde_voorbereiding` |
| Onderhoud, [Lokale herstelproef voor een pre-migratiedump](../onderhoud.md#updates-en-terugdraaien) en [Database-dump terugzetten](../onderhoud.md#database-dump-terugzetten). | Verifieer de controlesom of de versleutelde dump en importeer in een afzonderlijke database. Bereid de definitieve kopie vóór een oudere start opnieuw voor. | `test_versiewissel_annuleert_ook_op_terugvalkopie` (automatisch en handmatig), documentcontrole op de verwijzing in beide secties |
| Onderhoud, [Herstellen](../onderhoud.md#herstellen) en [Overstappen van een bestaande Compose-installatie](../onderhoud.md#overstappen-van-een-bestaande-compose-installatie). De [herstelwizard](../../apps/web/src/pages/system/Restore.tsx), installatiehandleiding, [A20](../platform-v2/04_BESTAANDE_APPS/A20_MY_HOUSE.md), [ADR-0004](ADR-0004-alles-in-een-custom-app.md) en het [wizardontwerp](../ontwerp/PEIL_MIGRATIE.md) beschrijven dezelfde terugweg. | Bij terugval na de overstap blijft ook de bronworker uit. Hervat na de gegevensvergelijking op de voorbereide kopie zodra het bronbewijs is verlopen. | `test_terugvalroutes_verwijzen_naar_de_offline_voorbereiding`, `test_versiewissel_met_logische_export`, `test_gepauzeerde_terugvalkopie_na_eindexport` |
| [I05, Terugdraaien / verwijderen](../platform-v2/03_INSTALLATIE/I05_MY_HOUSE.md), en de algemene uitleg over image-tags onder [Belangrijke begrenzingen](../platform-v2/00_START/LEES_MIJ_EERST.md#belangrijke-begrenzingen). | De beheerder zet de snapshot terug op aparte opslag en laat de huidige voorbereiding afronden vóór de vaste oude image start. | `test_terugvalroutes_verwijzen_naar_de_offline_voorbereiding` |
| Herstelmodus van API en worker, [Systeem](../api.md#systeem) in de API-documentatie, en de opslagfouten van de bewaker ([ADR-0015](ADR-0015-alles-in-een-gebouwd.md)). | De herstelmodus houdt de worker tegen op een nieuwer schema. Bij terugzetten van de snapshot geldt de voorbereiding op de kloon. | `test_schema_nieuwer.py`, `test_allinone_herstel.py`, documentcontrole |
| [Platformcontract](../platform-v2/PLATFORM_CONTRACTEN.md), [integrale testmatrix EX14/EX15](../platform-v2/05_TESTS/INTEGRALE_E2E_TESTMATRIX.md) en de [my_house-deltamatrix](../platform-v2/DELTA_MATRIX.md). | EX14 zet configuratie op een proefkopie terug. EX15 probeert een oude image op een nieuwer schema en verwacht een wachtende worker. Voor ingebruikname van een teruggezette database geldt de my_house-procedure in I05. | Documentcontrole van EX14/EX15 en `test_worker_start_niet_bij_nieuwer_schema_en_wacht` |
| [ADR-0016, Besluiten](ADR-0016-ondertekende-hub-verzoeken.md#besluiten), herstel van een gekoppelde instantie. | Bereid de snapshotkopie voor en controleer daarnaast de bevestigde Hub-koppeling vóór vrijgave van het schrijfpad. | `test_terugvalroutes_verwijzen_naar_de_offline_voorbereiding` |
| Startprogramma’s in `deploy/allinone` en hulpscripts onder `infra/scripts`, `infra/e2e` en `tools`. | De eenmalige bewaker opent alleen PostgreSQL en de voorbereidingsopdracht. De ontwikkel-, CI- en mutatiescripts starten geen vorige release op een herstelde snapshot. | `test_offline_start_gebruikt_alleen_postgres_en_de_huidige_voorbereiding`, `test_offline_start_weigert_lege_kopie`, broncontrole van de scripts |
