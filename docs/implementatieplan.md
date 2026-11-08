Versie 1.0 \| 8 november 2026 \| Smart Home Control

# 0. Projectbesluit en scope

Doel: één lokaal beheerde Smart Home Control-app, te installeren als één
TrueNAS SCALE Custom App (een Compose-stack met meerdere interne
services), die later op een andere Linux-server of in een andere woning
kan draaien. De applicatie is niet afhankelijk van Home Assistant.
Externe apps mogen nodig zijn voor de eerste fabrikant-pairing; daarna
heeft lokale werking prioriteit.

Huidige inventaris: Philips Hue Bridge V2 en bestaande Hue-knoppen; Gree
multisplit met twee binnenunits (wifi-indicatie aanwezig, nog niet
gekoppeld, exacte modellen onbekend); HomeWizard P1 en één Energy
Socket; elektrische boiler met stekker; airco op eigen 1-fasegroep;
TrueNAS SCALE. Sensoren: twee Zigbee temperatuur-/vochtigheidssensoren
en één ondersteunde USB-coördinator. Eigen app is een voorkeur, zonder
verplichte HA.

# 1. Vaste functies en expliciete uitsluitingen

  -----------------------------------------------------------------------
  **In scope**                        **Niet in scope**
  ----------------------------------- -----------------------------------
  Centrale web-UI en mobiele PWA,     Algemene no-code visuele
  login, realtime status              automatiseringsbouwer

  Hue lampen/groepen/scènes en        Raam- en deurdetectie
  configureerbare bestaande knoppen   

  2 Gree-units: verwarmen/koelen,     Garantie op lokale
  comfort- en handmatige modus        Gree-compatibiliteit vóór
                                      model-/wifi-test

  Zigbee sensoren, automatische       Ongecontroleerde boilersturing /
  pairing, batterij- en               afschakeling
  signaalbewaking                     

  HomeWizard P1, Energy Socket, later Onterechte claim van gemeten kWh
  1-fase kWh-meter                    per binnenunit

  Zelflerend klimaat, schema's,       Verplichte cloud of betaald
  comfortprofielen, experimenten      energiehistorieabonnement

  Installatieassistent, diagnoses,    Vaste koppeling aan één huis, IP,
  exports, alarms, portable backups   merk of NAS-pad
  -----------------------------------------------------------------------

# 2. Architectuur en repository

Gebruik een modulair monorepo: apps/web (React + TypeScript + Vite/PWA);
apps/api (Python FastAPI met REST en WebSocket); packages/contracts
(getypeerde API-schema's/OpenAPI en TypeScript-client); services/worker
(taken en collectors); integrations/{hue,gree,homewizard,zigbee,shelly};
services/mqtt (Mosquitto) en services/zigbee2mqtt; infra/compose;
migrations; docs; tests. PostgreSQL voor transactionele data en
tijdreeksmetingen. Gebruik een expliciete migrationstrategie (Alembic)
en reproduceerbare build.

Eén TrueNAS Custom App betekent één te installeren Compose-configuratie,
niet één container. Maak containers met gefixeerde images/versies,
healthchecks, restart policies, read-only waar mogelijk,
niet-rootprocessen, resourcegrenzen en persistente datasets.
USB-doorvoer met stabiel /dev/serial/by-id pad; fail-fast met duidelijke
melding indien niet aanwezig. LAN-discovery kan
netwerkmodus/advertenties vereisen; test per TrueNAS-versie en bied
altijd IP-invoer als fallback.

# 3. Data- en domeinmodel

-   Home(id, name, timezone, currency, active_from/to), Room(id,
    home_id, function, area_opt), Location/Server(id, deploy metadata).

-   Device(id, provider, kind, stable_identity, capabilities, lifecycle,
    home_id, discovered_at); DeviceBinding(logical_role_id,
    physical_device_id, effective_from/to).

-   LogicalRole (bijv. living_room_climate, boiler_energy), Capability
    (setpoint, hvac_mode, measured_power, temperature, button_event),
    unsupported-state semantiek.

-   SensorReading (timestamp UTC, received_at, device, metric, value,
    unit, quality, raw_reference); MeterCounter cumulative kWh met
    reset-/overflow-afhandeling.

-   ClimateZone, ControlProfile, Schedule, ManualOverride, SafetyPolicy,
    AdaptiveModelVersion, Experiment and ExperimentResult.

-   ButtonMapping (bridge, device, event type, context, action, conflict
    policy); command audit en actuator state.

-   Tariff (ingangsdatum, prijs, teruglevering, vaste kosten optioneel),
    EnergyAllocation en cost snapshots met provenance.

-   Alert, ConnectionHealth, EventLog, User/Role, SecretReference,
    Migration, BackupManifest en RebindPlan.

Normen: UUIDs voor logische objecten; geen IP als primaire sleutel. UTC
intern; lokale tijdzone per woning. Behoud bron en meetkwaliteit
(measured, derived, estimated, missing). Idempotente ingestie en unieke
eventidentifiers tegen dubbele telling; schema-versies in de export.
Historische data van oude woningen strikt gescheiden van nieuwe huizen.

# 4. Integratielaag -- contract

Elke adapter implementeert discover(), probe(), pair()/instructions(),
get_capabilities(), subscribe()/poll(), execute(command), health(),
normalize_state() en unpair(). Capabilities bepalen welke UI-acties en
automatiseringen zijn toegestaan; unsupported expliciet tonen, niet
simuleren. Retry met backoff en circuit breaker; geen onbeperkte
command-loops. Ruimte voor nieuwe adapters via gedocumenteerde SDK en
contracttests.

# 4.1 Philips Hue

-   Gebruik lokale Hue Bridge API v2; koppel via fysieke link-knop en
    bewaar bridge-auth veilig. Initialiseer lampen, kamers, scènes,
    apparaten en events via eventstream.

-   Knopmapping per event/knopmodel; bestaande Hue-functies eerst
    inventariseren. Afzonderlijke modus: uitsluitend verlichting,
    uitsluitend airco of gecombineerde actie als conflictvrij en bewust
    gekozen.

-   Bij bridge-uitval blijven lampen en bestaande Hue-regels waar
    mogelijk autonoom functioneren; app toont status en probeert te
    herstellen.

# 4.2 Gree multisplit

-   Koppel beide binnenunits eerst aan 2,4GHz-wifi via Gree+ / OEM-app;
    ontdek lokale IPs en toets protocolondersteuning per model en
    firmware. Wifi-symbool alleen garandeert dit niet.

-   Normaliseer get/set: aan/uit, heat/cool, setpoint, fan, swing indien
    ondersteund. Per-unit status, shared outdoor-unit constraint: geen
    conflicterende gelijktijdige HVAC-modi als die niet ondersteund
    worden.

-   Gebruik geteste bestaande protocolimplementatie waar licentie en
    veiligheid toestaan, zonder Home Assistant-runtimevereiste. Command
    acknowledgement/re-readback; rate limit; fallback op
    OEM-afstandsbediening bij uitval.

-   Implementatiefase 2 heeft expliciete compatibiliteitsgate: onbekend
    model -\> diagnostiek en supportmatrix, geen fictieve verbinding.
    Pairinggegevens en beveiliging veilig behandelen.

# 4.3 HomeWizard

-   Lokale API voor P1, Energy Socket en toekomstige HWE-KWH1.
    Capability detection op product_type, firmware en api_version. v1 én
    v2 ondersteunen; P1/kWh mogelijk v2, Energy Socket vooralsnog v1.

-   Metingen: huidig W, cumulatief kWh, import/export en gas waar
    beschikbaar. Poll-intervallen configureerbaar met throttling; meting
    en opslag staan los van fabrikantclouddiensten.

-   Bewaar ruwe metingen en uur/dag/maand/jaar aggregaten.
    Waterboiler-plug heeft read/measure-only beleid: commandfilter
    verbiedt switching; de stekker kan vanuit andere apps nog schakelen
    -- leg dit risico helder uit.

# 4.4 Zigbee en toekomst

-   MQTT-broker + Zigbee2MQTT als interne services; USB-coördinator en
    exact ondersteunde firmware verifiëren. Pairingscherm met venster,
    live resultaat, locatietoewijzing, kalibratie en batterijalarm.

-   Plaats de dongle met verlengkabel om interferentie te verminderen.
    Bewaak sensorstaleness: batterijgevoede sensoren rapporteren
    eventgedreven en niet gegarandeerd per seconde.

-   Abstraheer later Shelly, Matter, extra Zigbee, andere aircomerken
    als uitbreidingsadapters. Niet claimen dat alle merken zonder extra
    integratie werken.

# 5. Klimaatregelengine

Prioriteit van beslissingen: 1 hardware-/veiligheidslimieten, 2
handmatige override, 3 sensorvaliditeit, 4 tijdschema/profiel, 5
adaptief advies. Voor elke zone configuratie: doeltemperatuur,
comfortband, heating/cooling, energieprioriteit, min-interval voor
wijzigingen, max-setpoint-sprong, maximumstarts, watchdog, vacuüm van
externe sensordata.

-   Externe sensoren hangen op representatieve plek, niet in directe
    luchtstroom/zon; minimum/maximum plausibiliteit en gefilterde
    waarden met kwaliteitsindicator.

-   Houd inverterwerking in stand waar zinvol: niet blind aan/uit
    pendelen; pas airco-setpoint gecontroleerd aan. Geen
    apparaatbeveiligingen overrulen; OEM blijft leidend.

-   Een multisplit met gedeelde compressor kan het energiegebruik van de
    units niet individueel meten zonder extra apparatuur. Stuur
    vermogensoptimalisatie op het systeemniveau en comfort per ruimte.

-   Veilige terugval: bij stale sensor stoppen met externe bijsturing,
    laatste veilige instelling of OEM-regeling behouden, status melden;
    voorkom automatische heractivatie na bewuste handmatige uitzetactie.

-   Adaptief: eerst passief baseline verzamelen; modelschattingen van
    temperatuurrespons en vertraging per zone, versiebeheer en per-model
    rollback. Geen onbegrensde zelfaanpassende actuator; hard policy
    constraints en audit trail.

# 6. Energie, tarieven en experimenten

-   Totaalhuismeting via P1; apart multisplitverbruik via nieuwe
    1-fasemeter; boiler via reeds aanwezige Energy Socket indien veilig.
    Tarieven configureerbaar per tariefperiode; geen live tariefclaim
    zonder gekoppelde bron.

-   Bereken dag-/week-/maand-/jaarverbruik via cumulatieve meters waar
    beschikbaar; detecteer tellerresets, tijdzones, DST, ontbrekende
    intervallen en negatieve meetwaarden.

-   Beschrijf meetstatus nadrukkelijk: airco totaal gemeten, verbruik
    per binnenunit niet gemeten; P1 restverbruik kan berekend worden
    maar bestaat uit onbekende apparaten.

-   Experimenteer met vooraf ingestelde profielen (constant vs
    nachtverlaging vs preheat) op vergelijkbare weersomstandigheden.
    Gebruik buitentemperatuur alleen via expliciete sensor of optionele
    weersbron; rapporteer onzekerheid en confounders.

-   Toon comfortscore, temperatuurafwijking, schakelgedrag, kWh en
    euro's. Noem besparing alleen wanneer de meetopzet voldoende
    vergelijkbaar is; anders "indicatieve vergelijking".

# 7. Webinterface en gebruikersroutes

-   Startdashboard: woningen selector, statusgrid, twee klimaatkaarten,
    Hue, vandaag verbruik en alertfeed.

-   Klimaatpagina: zoneinstellingen, sensorselectie, modi, handmatige
    override, programma's, geschiedenis en aanpassingslog.

-   Verlichting: Hue-kamers, scènes en fysieke knoppen, inclusief
    conflictcontrole en simulatie van eventacties.

-   Energie: meters per object, trendgrafieken, tariefbeheer,
    kWh/kosten, exports, kwaliteit/missende data.

-   Optimalisatie: observatieperiode, modelstatus, experimenten,
    vergelijkingsrapport, opt-in/autopauze.

-   Apparaten: detecteren, toevoegen, eigenschappen, adapterstatus,
    vervangen, firmware/compatibiliteit, netwerkdiagnose.

-   Installatiewizard: TrueNAS preflight, Hue bridge pairing, HomeWizard
    API, Gree wifi via fabrikant, Zigbee USB, sensor pairing, meter
    toewijzen.

-   Verhuisassistent: nieuwe woning, back-up, restore, apparaten
    her-koppelen, capabilities vergelijken en kalibratie opnieuw
    starten.

-   Systeem: account, LAN-only, notificaties, database, backup health,
    logs, updates en beveiligingsmeldingen.

PWA: responsive desktop/mobiel, HTTPS voor veilige installatie buiten
localhost, app-iconen, offline "laatst bekend" tonen met verouderde
status; nooit offline knoppen tonen als actueel uitgevoerde actie.
Authenticatie en rolrechten standaard verplicht.

# 8. Verhuisbaarheid, back-up, restore en apparaatvervanging

1.  Maak export v1 als versleuteld en versieerbaar archive: app-config,
    schema/migrations, gebruikers en geautoriseerde geheimen,
    historische data, installatiemetadata, Zigbee2MQTT-data,
    coordinator-back-up indien ondersteund, checksum + manifest.

2.  Ondersteun beide scenario's: (A) nieuwe server, bestaande
    woning; (B) nieuwe woning met nieuw netwerk, eventueel vervangende
    apparaten. Wizard laat kiezen welke house-id behouden/archiveren.

3.  Start herstel in veilige read-only/discovery mode: nooit automatisch
    apparaten schakelen vóór matching en testen. Vind oude apparaten op
    persistent identifier, niet IP; vervanging via logical role binding.

4.  Markeer per functie compatibel/deels compatibel/niet beschikbaar;
    onbeschikbare acties inactief met suggestie. Oude historie blijft
    aan oorspronkelijke fysieke meter en woning gekoppeld.

5.  Kalibratie van een nieuwe woning/airco opnieuw starten, maar
    comfortprofielen en tijdschema's wel beschikbaar houden als
    concepten.

6.  Oefen herstel periodiek op tweede tijdelijke Compose-installatie en
    vergelijk checksums, aantallen records, sensorbindingen en
    onboardingstatus. Meld wanneer Zigbee herpair nodig is.

# 9. Privacy en beveiliging

-   LAN-first; geen standaard internet-expositie, port forwarding of
    externe analytics. Gegevens lokaal; geen cloud-login verplicht na
    initiële fabrikantsetup.

-   Gebruikerslogin met veilige wachtwoordhash, sessie-Cookie
    Secure/HttpOnly/SameSite, CSRF, rollen admin/viewer, audit en rate
    limiting. Geen default adminwachtwoord.

-   Secrets encrypted-at-rest of via Docker secrets/host-config; exports
    met sterke passphrase en secret-redactionoptie. Credentials nooit in
    logs of frontend.

-   Least privilege voor USB/network; Mosquitto credentials en ACL; API
    inputvalidatie; signed/pinned images; dependency scan, backups en
    update rollback.

-   Boiler: het stroomonderbrekende commando wordt door adapter én
    policy-engine verboden, ook bij generieke regels.
    Energiemeterapparaten kunnen hardwarematig wel een relais hebben: UI
    en backend houden dat uit voor beschermde roles.

# 10. Betrouwbaarheid en gegevensbeheer

-   PostgreSQL opslag op persistente TrueNAS datasets, dagelijkse
    databaseback-up plus wekelijkse volledige portable export;
    herstelpunt en retentie configureerbaar.

-   Raw time-series partitioneren of tijdgebonden retentie met
    geaggregeerde lange-termijnseries; export CSV/Excel, JSON en
    optioneel Parquet.

-   Systeemstatus: missed polls, sensor stale, CPU/RAM, vrije disk, MQTT
    status, USB serial status, DB migrations, back-up freshness en Gree
    health.

-   Time-sync/NTP check, crash-only/restart recovery, idempotente
    workers, reconnect backoff, startveiligheid: na power loss worden
    geen automatisch onverwachte apparaatcommando's verzonden.

# 11. Fasen en oplevercriteria

  ---------------------------------------------------------------------------------
  **Fase**                **Werk**                          **DoD / harde
                                                            acceptatie**
  ----------------------- --------------------------------- -----------------------
  0 Discovery             Gree                              Compatibility matrix en
                          model-/wifi-/protocolvalidatie,   preflightrapport;
                          boiler watts, USB, netwerk        onzekerheden zichtbaar

  1 Basisplatform         Monorepo, compose, login, DB,     Verse TrueNAS
                          PWA, updates, backups             installatie; database
                                                            reboot; inloggen;
                                                            restore demo

  2 Lokale adapters       Hue v2, HomeWizard v1/v2, Gree    Echte discovery +
                                                            read + command tests;
                                                            Gree gate indien
                                                            unsupported

  3 Zigbee                MQTT, Z2M, temp/humidity, pairing Twee sensors gekoppeld;
                                                            stale/batterij alerts;
                                                            USB replug herstelt

  4 Klimaat               Profielen, handmatig, comfort,    Geen rapid cycling;
                          policies                          overrides
                                                            gerespecteerd; stale
                                                            sensor fallback

  5 Energie               P1, Socket, kWh-meter, kosten,    Accurate cumulatieve
                          export                            dagtotalen; meterreset;
                                                            backfill; geen dubbele
                                                            data

  6 Adaptief              Baseline, experimenten, model     Opt-in, meetbare
                          audit                             criteria, rollback,
                                                            expliciete onzekerheid

  7 Verhuisbaarheid       Backup, restore, rebind, nieuw    Migratie naar andere
                          huis                              Linux host, nieuw
                                                            netwerk en andere
                                                            apparaten getest

  8 Oplevering            Security, docs, e2e, upgrades     Alle hieronder genoemde
                                                            tests groen;
                                                            Nederlandstalige in-app
                                                            hulp
  ---------------------------------------------------------------------------------

# 12. Verplichte tests en scenario's

-   Unit/contracttests per adapter met vaste recorded fixtures; nooit
    mock-success als bewijs van echte device-integratie.

-   End-to-end browserflows voor onboarding, Hue-knopconflict, meter
    toewijzing, klimaatprofiel, export, notificatie en herstel.

-   LAN-verbinding kwijt / bridge offline / Gree offline / sensor stale
    / MQTT restart / USB weg / PostgreSQL restart.

-   Gree handmatige afstandsbediening gebruikt tijdens actieve
    comfortregeling; automatiek respecteert override.

-   Twee binnenunits vragen tegenstrijdige heat/cool: policy voorkomt
    ongeldige commands en toont uitleg.

-   Boiler socket: alle API-routes, gebruikersrollen en
    automatiseringshooks kunnen nooit off/on uitvoeren.

-   P1-kWh reset, import/export, DST-wissel, dubbele events en
    ontbrekende stroommetingen.

-   TrueNAS reboot zonder dataverlies, app update en rollback, herstel
    op generieke Linux-machine.

-   Nieuwe woning, andere wifi-IP-ranges, andere airco en vervangen
    sensor: historie en rolbinding correct.

-   Integriteit: back-up met corruptie weigeren, migraties atomisch,
    rapportage over gefaalde herstelstappen.

# 13. Installatie en eindgebruikersdocumentatie

Lever README, architectuurplaat, onderhoudshandleiding, SECURITY.md,
lijst ondersteunde modellen en versies, troubleshooting en
gestructureerde Nederlandse wizard. Toon voor elke fabrikant duidelijk
wat rechtstreeks in de app werkt, en welke externe initiële koppeling
nodig is. Bied stap-voor-stap foto-/tekstplaatsen voor Hue link-knop,
Gree+, HomeWizard lokale API en Zigbee pairing.

# 14. Werkopdracht voor Claude Code / Codex

Voer dit plan in een bestaande of nieuwe repository stapsgewijs uit.
Begin met repository- en hostinventarisatie, vastgelegde
architectuurbeslissingen en de hardware-compatibiliteitsgate.
Implementeer vervolgens alle fasen inclusief echte tests, documentatie
en de complete TrueNAS Compose-stack. Geen stubs of fictieve dashboards
als eindresultaat: bij ontbrekende hardware een heldere "nog niet
getest" status en automatische contracttests. Behoud alle afgesproken
functies, werk in kleine controleerbare commits, log de uitgevoerde
tests, en lever één installeerbare release met upgraderoute en
herstelprocedure. Stop veilige hardware-implementatie als het specifieke
Gree-model onbekend/incompatibel is, maar bouw de rest volledig uit.
Vereis menselijke bevestiging voor elektrische werkzaamheden,
port-openingen, destructieve acties en onomkeerbare migraties.

# 15. Open punten (niet blokkeren voor appontwikkeling)

-   Gree typenummers van beide binnenunits en buitenunit; Gree+
    wifi-koppeling en lokale protocoltest.

-   Boiler typeplaatje in watt en geschikte wandcontactdoos voor Energy
    Socket.

-   TrueNAS-versie en USB passthrough voor gekozen Zigbee dongle.

-   Elektricien bevestigt aparte 1-fasegroep, beschikbare ruimte en
    volledig multisplitmeetpunt.

-   Exact te bestellen sensor-SKU en compatibele coordinatorfirmware.

# 16. Referenties en gecontroleerde documentatie

TrueNAS Custom Apps:
https://www.truenas.com/docs/scale/apps/installcustomappscreens/

HomeWizard API:
https://api-documentation.homewizard.com/docs/introduction/

HomeWizard 1-fasemeter:
https://www.homewizard.com/nl/shop/wi-fi-kwh-meter-1-fase/

HomeWizard Energy Socket specs:
https://www.homewizard.com/nl/shop/wi-fi-energy-socket/

Zigbee2MQTT adapters: https://www.zigbee2mqtt.io/guide/adapters/

Zigbee2MQTT Aqara WSDCGQ11LM:
https://www.zigbee2mqtt.io/devices/WSDCGQ11LM.html

Gree Climate compatibiliteit (referentieimplementatie):
https://www.home-assistant.io/integrations/gree

Gree lokaal protocol (voorbeeldbibliotheek):
https://github.com/RobHofmann/HomeAssistant-GreeClimateComponent
