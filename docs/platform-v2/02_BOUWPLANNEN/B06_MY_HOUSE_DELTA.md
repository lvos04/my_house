# my_house Smart Home integratie — volledige bouwspecificatie

**Type/eigenaar:** Bestaand: lvos04/my_house  
**Doel:** Koppel my_house v0.1.0 zonder klimaatregeling, energietellers of veiligheidsregels te dupliceren.

**Afhankelijkheden:** Zigbee hardware indien aangeschaft, NAS services, Hub.

**Bron:** https://github.com/lvos04/my_house

## 1. Concrete schermen en gebruikersreizen
Woningoverzicht; Kamer; Klimaat; Lampen; Energie; Apparaten; Sensoren; Meldingen; Aanpassen/verhuizen; Hub-aggregaatkaart

**Per scherm minimaal:** responsief (360/390/768/1440 px), status leeg/laden/storing/offline, gedocumenteerde navigatie, zoek/filter/paginering, rolcontrole, datum van laatste update, bronherkomst, fout in het Nederlands, toegankelijk toetsenbordgebruik en zichtbaar auditspoor voor wijzigingen.

## 2. Datamodel en levensduur
home, room, zone, device, role_binding, measurement, energy_total, command, feedback, override, backup_manifest blijven in my_house; Hub alleen cache/measurement metadata

Dwing database-migraties met forward-schema-tests af; time-series met retentie/aggregatie; primaire gegevens alleen bij bron. Entiteiten krijgen stable ID, versie, eigenaar, timestamps en provenance waar toepasselijk. Secrets alleen als vault-reference.

## 3. API en integratie-contracten
Bestaande `/api/v1/homes/{id}/overview`, `/zones/{id}`, `/zones/{id}/command`, `/homes/{id}/...` volgens docs/api.md; eerst Swagger/live versie vergelijken.

Bij alle endpoints: authenticatie, rol, inputvalidatie, idempotency voor mutaties, paginering of cursors voor lijsten, rate limits, time-outs, versie/capability-detectie en machineleesbare foutcodes. De genoemde uitbreidingsroutes zijn ontwerpeisen, niet automatisch bestaande endpoints. API-contracten moet de bouwer in OpenAPI vastleggen en e2e verifiëren.

## 4. Bouwfasen (verplichte volgorde)

### F0 - Inventaris & onderzoek
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B06-001 — Productiecompatibiliteit
**Status bij start:** Controleren; **impact:** Hoog.
**Gewenst gedrag en grenzen:** 0.1.0 release bestaat maar echte Hue, Gree, HomeWizard en Zigbee niet aantoonbaar getest.
**Implementatie in code/diensten:** Maak testmatrix voor apparaatmodel, firmware, protocolversie, LAN-IP, detectie en terugval.
**Schermen/bediening:** Apparaatkaart met compatible/tested/unsupported, met datum.
**Acceptatie / concrete toets:** Geen ongeteste Gree-bediening; Hue en P1 als eerste read-only.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F1 - Kern en contracten
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B06-002 — Apparaatconnecties
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Hue local API v2 + eventstream, HomeWizard local API v1/v2, Gree UDP opt-in bind/readback, Zigbee2MQTT MQTT.
**Implementatie in code/diensten:** Reuse bestaande adapters, check mDNS/broadcast container limitation, DHCP reservation, serial-by-id for dongle.
**Schermen/bediening:** Testknop per adapter en apparaat met feedback.
**Acceptatie / concrete toets:** Zonder readback nooit ‘command uitgevoerd’ zeggen; reconnect na netwerkonderbreking.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

#### B06-003 — Harde boilerschakelblokkade
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Boiler Energy Socket is uitsluitend energiemeter; app, Hub of AI mogen niet schakelen.
**Implementatie in code/diensten:** Deny switching on API, worker and central delegated command, regression tests.
**Schermen/bediening:** Boilerkaart heeft vermogen/kWh/kosten, geen schakelknop.
**Acceptatie / concrete toets:** Probeer POST vanuit Hub/admin/AI: altijd policy_denied.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F2 - Functionaliteit
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B06-004 — Gree klimaat en gedeelde buitenunit
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Twee Gree binnenunits delen compressor: conflicterende heat/cool mode voorkomen en handmatige remote overrides bewaren.
**Implementatie in code/diensten:** Bestaande climate core met shared unit invariant; sensor stale fallback; safe min interval.
**Schermen/bediening:** Zones tonen modus, target, active sensor, freshness, override en reden voor blokkade.
**Acceptatie / concrete toets:** Remote-off blijft off; heat/cool conflict HTTP409; stale Zigbee stopt optimalisatie.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

#### B06-005 — Energiedatakwaliteit
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** P1 totaal, boilerplug, airco 1-fase kWh meter; rollen los van fysieke devices; datumcorrecties voor meterreset/zomertijd.
**Implementatie in code/diensten:** Read cumulative counters and quality tags, decompose measurements; aggregate only when sources comparable.
**Schermen/bediening:** Dag/week/maand/jaar, prijs, verbruiksbron en realiteit vs schatting.
**Acceptatie / concrete toets:** Meters vervangen → historie correct; geen verbruik per airco binnenunit verzinnen.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F3 - Integraties en veiligheid
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B06-006 — Hub-integratie
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Hub toont woning, zones, meldingen, energie, metingen, geen dubbele eigen Gree-controller.
**Implementatie in code/diensten:** Read-only overview/events; commands only through my_house API with permission, TTL and readback.
**Schermen/bediening:** Compacte huiskaart en drilldown/quick controls die rollen volgen.
**Acceptatie / concrete toets:** my_house offline → oude data met stale mark; Hub kan niet schakelen.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

#### B06-007 — Spraak en AI
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** AI zet alleen een veilig schema-aanbeveling of lichtcommando op; climate high-impact approval en boiler denied.
**Implementatie in code/diensten:** Tool schemas per zone ID, user role, before/after confirm, physical state check.
**Schermen/bediening:** Chat antwoordt met bevestigde device-status, niet verstuurd=klaar.
**Acceptatie / concrete toets:** Onbetrouwbare Gree readback → actie mislukt, geen schijnsucces.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F4 - Automatisering
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B06-008 — Verhuisbaarheid
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Nieuw huis/andere NAS, apparaten vervangen zonder meters/geschiedenis te verliezen.
**Implementatie in code/diensten:** Export encrypted home config, device logical IDs and role mapping, import wizard + credential reassociation.
**Schermen/bediening:** Verhuisassistent met dry-run en dubbele apparaat-detectie.
**Acceptatie / concrete toets:** Verhuizing naar mock NAS behoudt zones/historie; nieuw IP vereist geen datareset.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

#### B06-009 — Hardwarecontrole
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Zigbee USB, Hue, Gree, HomeWizard echt testen en Android QR push+widget; installatie stack 25.10 compat.
**Implementatie in code/diensten:** Use docs/testlog hardware checklist, full docker compose, reboot, cloudflared access scope.
**Schermen/bediening:** Testlog met echte apparaatmodellen.
**Acceptatie / concrete toets:** Geen productie-approved status vóór echte device tests.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

## 5. Niet-regressie, randgevallen en verbodsbepalingen
Hardware niet als reeds aanwezig beschouwen: Zigbee-stick/sensoren en energieplug/kWh-meter kunnen nog aangeschaft moeten worden. my_house is onafhankelijk van Home Assistant en de app ondersteunt een lokale PWA plus Android.

## 6. Oplevering door bouwer
Opleveren: (1) broncode en tests, (2) migration scripts en versiecompatibiliteit, (3) Compose/TrueNAS app manifest met gepinde images, (4) .env.example zonder geheimen, (5) OpenAPI/connector schemas, (6) test- en auditrapport, (7) restore bewijs, (8) release notes, (9) gebruikershandleiding, (10) screenshots web/mobiel. Markeer niet-geteste hardware/echte accounts als `NIET GEVALIDEERD`.

## 7. Proof of Done
Alle acceptatiecriteria in Excel zijn gekoppeld aan testbewijs en codecommit; bij stroomonderbreking, uitval van afhankelijke app, NAS-herstart, gewijzigde credentials en incompatibele versie degradeert de service aantoonbaar veilig. Geen directe writes aan de database van een andere app.
