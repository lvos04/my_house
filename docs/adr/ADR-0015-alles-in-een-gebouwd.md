# ADR-0015 — Alles-in-één-image voor my_house

- **Status** Image gebouwd in S-P3-720. CI-rooktest en digestcontrole toegevoegd in S-P3-725.
- **Bronnen** ADR-0004, besluit 6 zoals ingevuld door Z-17, en PLATFORM_CONTRACTEN.md §2.2.

## Start en opslag

De image bevat PostgreSQL 16 met bijpassende client, de API, de worker, nginx met de gebouwde webapp, mosquitto en Zigbee2MQTT 2.14.3 met de Node.js-runtime uit datzelfde gepinde artefact. De PostgreSQL-server, client en extensies komen uit één image met een vaste digest. De Debian-pakketten komen uit de ondertekende snapshots van 15 november 2026. De webproxy biedt poort 8080 aan. PostgreSQL accepteert verbindingen via de unix-socket met peer-authenticatie. De API en de MQTT-broker luisteren alleen op loopback. Nginx bepaalt zelf de doorstuurheaders. De tunnel staat uit. Het bestaande gedrag van `HUB_URL` blijft beschikbaar via `/_shc/config.json` volgens Z-08.

De bewaker is PID 1. Hij vergrendelt de opslag en controleert de indeling vóór een eigendomswijziging. Bij oude Compose-opslag, een onbekende indeling of ontbrekende sleutels stopt de start. Een rootstart kan de herkende opslag daarna overdragen aan gebruiker en groep 568. Hij leegt de bounding set en laat daarna de diensten als 568 met `no_new_privs` draaien. Bij een rechtstreekse start als 568 vereist hij schrijfbare opslag en laat hij de standaard Docker-bounding-set toe volgens §2.2. De exec-healthcheck voert die rechtenwissel zelfstandig uit. Het korte venster vóór de wissel blijft een restrisico.

De bewaker maakt bij de eerste start de mappen `postgres/`, `app/`, `backups/`, `secrets/` en `mosquitto/` in `/appdata` aan. Bestaande geheime bestanden behouden hun inhoud. Alleen bij lege opslag maakt de app haar sleutels met modus 0600. Een bestaande installatie met een ontbrekend geheim heeft de oorspronkelijke snapshot nodig. De broker gebruikt de bestaande MQTT-ACL. De bewaker berekent de gezouten Mosquitto-hashes in het geheugen voordat hij het wachtwoordbestand aanmaakt.

Na PostgreSQL draait de bestaande migratie-ingang. Vóór een upgrade controleert de app de SHA-256 en de volledige inhoud van de pre-migratiedump. Alle openstaande revisies delen één transactie. Na een fout in een vervolgrevisie blijven de oorspronkelijke clientrechten en gegevens beschikbaar. Bij een nieuwere revisie bedient nginx de bestaande herstelinfo van de API. De worker blijft uit en `/readyz` geeft 503. Bij een normale start toetst `/readyz` de database en haar schemarevisie voordat de bewaker de worker start. De exec-healthcheck combineert nginx `/healthz`, API `/readyz` en een actuele workerhartslag.

## Zigbee2MQTT volgens Z-17

Z-17 kiest variant a van ADR-0004. Met een bruikbaar tekenapparaat op `/dev/zigbee` start de optionele dienst zodra mosquitto een geauthenticeerde verbinding accepteert. Bij een rootstart kan de bewaker uitsluitend aan de Zigbee-procesboom de benodigde apparaatgroep meegeven. Groep 0 is uitgesloten. Zonder lees- en schrijfrecht blijft Zigbee2MQTT uit. Bij een gevulde bounding set blijft Zigbee eveneens uit. De bewaker geeft Node uitsluitend de vaste Zigbee-omgeving mee. Landlock begrenst de Zigbee-procesboom tot de runtime, de eigen gegevens en de benodigde apparaatbestanden. Ontbreekt deze kernelvoorziening, dan blijft de optionele dienst uit. Zonder tekenapparaat blijft die stand stil.

De app gebruikt `/appdata/zigbee2mqtt/` voor het Zigbee-netwerk en de MQTT-inloggegevens. Ze vult bij de eerste start ontbrekende bestanden aan en behoudt eerder opgeslagen sleutels. `SHC_ZIGBEE_ADAPTER` kiest de eerste adapter en heeft standaard de waarde `ember`. Zigbee2MQTT gebruikt dezelfde interne broker met een eigen gebruiker en wachtwoord. De webinterface van Zigbee2MQTT staat uit. Valt de dienst uit, dan probeert de Zigbee-bewaker hem na oplopende wachttijden opnieuw te starten. De diagnose van een ingestelde Zigbee-integratie blijft zichtbaar via de bestaande donglecheck. De gewone container-healthcheck blijft de kerndiensten toetsen.

De volledige back-up neemt de Zigbee-map op wanneer die aanwezig is. Een geredigeerde export bevat uitsluitend bekende gewone configuratievelden en lege geheimvelden. Coördinatorback-ups en andere kopieën van de netwerksleutel horen bij de volledige export. Bij geredigeerd herstel behouden bestaande Zigbee-configuratie en geheimen hun inhoud. De bestaande compose-installaties behouden hun standaardlocatie voor die back-upbron.

## Validatiegrens

De containerproeven in `infra/tests/allinone_container.py` gebruiken een lokaal nep-apparaat en een gevulde proefdatabase. Installatie als standaard Custom App en leesrechten op de echte dongle zijn **NIET GEVALIDEERD (echte NAS)**. De echte Zigbee-dongle is **NIET GEVALIDEERD (hardware)**. Zie D01, D13 en N-P6-06. Het manifest en de veldbeschrijving volgen in S-P3-755.

De CI-job `allinone` draait de HTTP-rooktest binnen de proefcontainer zonder apparaat. De test controleert onder meer de WebSocket, de back-up zonder Zigbee-opslag en de antwoorden van nginx nadat de API is gestopt. Bij de proef met het nep-apparaat controleert de suite dat de aparte back-upproef Zigbee-opslag bevat. De browserjob `e2e` levert het browserbewijs met de testgegevens en procesbesturing van de bestaande runner.

De ontwikkel-Compose gebruikt vaste digests als standaard. Voor eigen builds kan de stack een expliciete `SHC_IMAGE_PREFIX` en `SHC_VERSION` krijgen. CI controleert iedere externe Dockerfile-basisimage op een vaste digest. De release- en edge-workflows schrijven de ondertekende API- en webverwijzingen naar `release/digests.json` en bewaren dat bestand als workflowartefact `release-digests`. Bij een nieuwe standaardversie horen de API- en webdigests in de ontwikkel-Compose en de API-digest in de E2E-overlay bij dezelfde publicatie.
