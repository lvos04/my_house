# Onderhoud, back-up, herstel en verhuizen

In deze handleiding is `<root>` de map met je gegevens (bijvoorbeeld `/mnt/tank/apps/smart-home`) en
`<api-container>` de naam van de API-container. Die vind je met `docker ps`; op TrueNAS heet hij
meestal `ix-smart-home-api-1` (de andere containers heten `…-worker-1`, `…-db-1` enzovoort).

Commando's met `shc-api` voer je uit in de API-container, bijvoorbeeld
`docker exec -it <api-container> shc-api verify-backup /backups/<bestand>.shcbak`. In die container is
`<root>/backups/` te zien als `/backups` en `<root>/app/` als `/data`. Vraagt een commando om de
wachtwoordzin van de back-up, dan kun je die ook meegeven met `--passphrase-file <pad>` of
`--passphrase-stdin`.

## Updates en terugdraaien

1. Maak een volledige back-up en controleer of deze leesbaar is. Stop de app en maak een snapshot van de hele opslag, inclusief de geheimen.
2. Kies de nieuwe versie in het Custom App-formulier. Zie [Installatie op TrueNAS](installatie-truenas.md#updaten-en-terugdraaien).
3. Bij een oudere database schrijft de API vóór de upgrade `pre-migrate-*.sql.gz` en de bijbehorende `.sha256` in de back-upmap. De hele reeks revisies draait in één transactie. Na een geweigerde dump of mislukte revisie blijft de database op de oorspronkelijke revisie.
4. De worker start pas als de API gezond is, dus nooit tegen een half bijgewerkte database.

**Database van een nieuwere versie.** Start een oudere versie van my_house op een database die een nieuwere versie al
heeft bijgewerkt, dan herkent de API dat aan de databaserevisie: die staat niet in de migraties van deze versie. Dat
gebeurt vóór alles: er komt geen dump, geen migratie en geen enkele schrijfactie. In plaats van steeds opnieuw te
starten, start de API in een **herstelmodus**. Elke vraag aan de API krijgt dan de uitleg *De database is van een
nieuwere my_house-versie; kies in het formulier van de Custom App de bijpassende versie of zet de snapshot van vóór de
update terug.* De status van de API (`/api/v1/health`) meldt `herstelmodus`, zodat de app niet als gezond geldt;
`/api/v1/system/recovery` geeft dezelfde uitleg met de revisie van de database en de nieuwste revisie die deze versie
kent. De worker start geen rondes en wacht. my_house zet een database nooit zelf terug naar een oudere versie: kies in
het formulier van de Custom App weer de versie die bij de database hoort, of zet de snapshot van vóór de update terug
en start dan deze versie opnieuw.

**Terugdraaien.** Gebruik in het formulier de vorige versie met de bijbehorende snapshot op aparte opslag. Controleer de woning en de versleutelde koppelingen voordat je de nieuwe installatie gebruikt.

**Lokale herstelproef voor een pre-migratiedump.** Bewaar dump en controlesom in dezelfde map. `shc-api verify-migration-dump <dump> --out <nieuw.sql>` valideert de SHA-256, de gzip-integriteit en het einde van de SQL-export. Het uitvoerbestand moet nieuw zijn. Importeer de gecontroleerde SQL vervolgens met `psql -X --single-transaction -v ON_ERROR_STOP=1 -d <lege-proefdatabase> -f <nieuw.sql>` in een afzonderlijke lege database. Vergelijk revisie en tabelinhoud met de voorstaat en bewaar de originele geheimen voor het lezen van versleutelde gegevens. Gebruik voor oude dumps zonder opgeslagen controlesom een onafhankelijk geverifieerde snapshot of back-up.

## Back-ups

### Automatisch (aanbevolen)

Zet automatische back-ups aan bij **Systeem › Back-ups en bewaren › Automatische back-ups**:

- **Wachtwoordzin** (minimaal 12 tekens). Die wordt versleuteld opgeslagen met de hoofdsleutel van de
  server (`secrets/secret_key`) en nooit meer getoond. Bewaar hem ook in je wachtwoordmanager: zonder
  wachtwoordzin is geen enkele back-up terug te zetten.
- **Tijdstip:** het hele uur waarop de back-up begint, in de tijdzone van de woning (standaard 03:00).
- **Aantal bewaren:** hoeveel automatische back-ups blijven staan (standaard 14, 1–365).
- **Wat maken:**
  - *Elke dag een volledige export* (standaard): een `.shcbak` met alles, ook de Zigbee2MQTT-gegevens.
  - *Elke dag een database-dump, elke week een volledige export*: meestal een kleinere, snellere
    `.shcdump` (alleen de database), en minstens elke 7 dagen een volledige export. De eerste
    automatische back-up na het omzetten is een volledige export.

Hoe het werkt:

- **Inhalen.** Stond de NAS uit op het ingestelde uur? Dan begint de back-up bij de eerste gelegenheid
  na dat uur, zodra de laatste automatische back-up ouder is dan 23 uur.
- **Mislukt?** Dan verschijnt de melding *Automatische back-up mislukt* in de app (en een push-bericht
  als je dat hebt ingesteld, zie [Meldingen buiten de app](#meldingen-buiten-de-app)). De worker probeert
  het opnieuw na 1 uur, dan 2 uur, enzovoort, tot maximaal 6 uur ertussen. Nooit direct opnieuw. De
  melding verdwijnt na de eerstvolgende geslaagde back-up.
- **Onderbroken?** Werd de worker (of de hele container) herstart terwijl een back-up liep, dan telt dat
  als mislukte poging met dezelfde wachttijd: de melding zegt *Back-up onderbroken (worker herstart)*,
  de back-up staat in de lijst als mislukt en het half geschreven tijdelijke bestand wordt opgeruimd.
- **Opruimen.** Alleen automatische back-ups worden opgeruimd (bestand én regel in de lijst), exports en
  dumps elk apart tot het ingestelde aantal. Handmatige back-ups en de dumps van vóór een update blijven
  altijd staan.
- **Geheugen.** Een back-up draait in een apart proces dat in stukjes naar schijf schrijft; de worker en
  de API blijven gewoon werken.
- Het overzicht toont bij *Back-up* hoe oud de laatste goede back-up is. Ouder dan 36 uur, of
  Zigbee-gegevens die ontbreken terwijl je Zigbee gebruikt: oranje.

Vroeger werd de wachtwoordzin in het bestand `<root>/app/backup_passphrase` gezet. Dat werkt nog als er
in de app geen wachtwoordzin is ingesteld, maar stel hem liever in de app in.

### Handmatig

**Systeem › Back-ups en bewaren › Nu een back-up maken**, met een wachtwoordzin en de keuze om geheimen
weg te laten. Of: `docker exec -it <api-container> shc-api backup` (met `--dump` voor alleen een
database-dump). Bij **Gemaakte back-ups** kun je elke back-up downloaden.

### Wat erin zit

- **Volledige export (`.shcbak`):** alle instellingen, gebruikers, apparaten, rollen, metingen,
  schema's, profielen en tarieven, de Zigbee2MQTT-gegevens (netwerksleutel en apparatenlijst) en, tenzij
  je *geheimen weglaten* kiest, de versleutelde apparaatsleutels en wachtwoord-hashes. Inlogsessies en
  koppelcodes zitten er nooit in.
- **Database-dump (`.shcdump`):** alleen de database (pg_dump), versleuteld. Geen Zigbee2MQTT-gegevens.
  Terugzetten gaat met de hand, zie [Database-dump terugzetten](#database-dump-terugzetten).
- **Hub-koppeling:** de gepinde sleutelset, gebruikte nonces en opgeslagen idempotentieantwoorden
  blijven buiten nieuwe exports en database-dumps. Bevestig een bestaande Hub-koppeling opnieuw na herstel.
- **Zonder geheimen** (geredigeerd): de export bewaart bekende gewone Zigbee-instellingen en lege
  geheimvelden. De netwerksleutel, coördinatorback-ups, overige Zigbee-bestanden, apparaatsleutels en
  wachtwoord-hashes vallen buiten deze export. Bij herstel blijven bestaande Zigbee-configuratie en
  geheimen behouden. Na terugzetten is
  `shc-api reset-password` nodig en moet je apparaten opnieuw koppelen.
- **Versleuteling:** AES-256-GCM in blokken van 1 MB, met een sleutel uit je wachtwoordzin (scrypt).
  Elk bestand in het archief heeft een SHA-256-controlegetal. Een beschadigd of aangepast bestand wordt
  geweigerd.

### Bewaren buiten de NAS

- Bewaar de **wachtwoordzin** en de oorspronkelijke hoofdsleutel in je wachtwoordmanager buiten de NAS.
  De Custom App gebruikt `secrets/app-key`. De vorige Compose-installatie gebruikt `secrets/secret_key`.
  Zonder die sleutel zijn de apparaatsleutels in de back-up onleesbaar. Dan moet je Hue, HomeWizard (API v2) en
  de Gree-units opnieuw koppelen en de wachtwoordzin en het push-adres opnieuw instellen in de app.
- Maak TrueNAS-snapshots van de dataset (die blijven op de NAS).
- **Kopie buiten huis (replicatie): kopieer alleen `<root>/backups/`.** Nooit `app/`, `secrets/` of
  `postgres/`: daar staan de hoofdsleutel en de database, waarmee de opgeslagen wachtwoordzin te
  ontsleutelen is. Een kopie van alleen `backups/` is zonder je wachtwoordzin onbruikbaar. Op TrueNAS:
  maak van `backups` een eigen dataset (child dataset) en repliceer alleen die, of beperk de
  replicatietaak tot die map.

## Herstellen

### In de app (aanbevolen)

**Systeem › Herstellen en verhuizen**, als beheerder in de web-app:

1. **Back-up kiezen en controleren.** Kies een `.shcbak` (tot 2 GB) en vul de wachtwoordzin in. De app
   uploadt en controleert het bestand zonder iets te wijzigen, en toont: datum en versie, het aantal
   records per tabel, woningen, apparaten en koppelingen, of de Zigbee2MQTT-gegevens erin zitten, en of
   de apparaatsleutels, de wachtwoordzin voor back-ups en het push-adres te ontsleutelen zijn met de
   hoofdsleutel van **deze** server.
   - Een back-up van een **nieuwere** versie wordt geweigerd: werk de app eerst bij.
   - Een back-up van een **oudere** versie wordt na het terugzetten bijgewerkt. Het rapport noemt wat
     niet (volledig) is overgenomen.
2. **Kiezen:** *A · Zelfde woning herstellen* of *B · Nieuwe woning (verhuisd)*, zie
   [Verhuizen](#verhuizen). Kies **Herstelproef uitvoeren** om eerst terug te zetten in een
   tijdelijke database. Vergelijk per tabel de aantallen in de back-up en de proef.
3. **Terugzetten.** De achtergronddienst pauzeert (onderhoudsmodus) en alles wordt in één keer
   vervangen. Daarna sta je in de **veilige modus**: er gaat geen enkel commando naar een apparaat. Je
   wordt uitgelogd (inlogsessies zitten niet in een back-up); log opnieuw in.
4. **Vergelijken: wat werkt er?** Per apparaat en per functie (rollen, airco en sensor per zone,
   lichtgroepen): *Werkt*, *Gedeeltelijk*, *Niet beschikbaar* of *Nieuw gevonden*, met een suggestie.
   Werkt alles zoals verwacht, kies dan **Veilige modus opheffen**.

### Overstappen van een bestaande Compose-installatie

1. Maak in de bestaande app via **Systeem › Back-ups en bewaren** een volledige versleutelde export
   met **Geheimen meenemen**. Download hem en bewaar hem buiten de bronapp. Bewaar de oude installatie
   met eigen opslag, hoofdsleutel en database als terugval.
2. Installeer de standaard Custom App via het [TrueNAS-formulier](installatie-truenas.md). Kies nieuwe
   opslag. Maak alleen een tijdelijk beheerdersaccount aan en ga direct naar **Systeem › Herstellen en verhuizen**.
3. Bevries wijzigingen en de verwerking van metingen op de bron vóór je definitieve export. Maak en
   download die export via **Back-ups en bewaren** en zet de bronapp daarna stil. Gebruik het doel pas
   wanneer je kunt vaststellen dat de definitieve export alle brongegevens bevat. Kun je de broninvoer
   tijdens het maken van die export niet stilhouden, blijf dan op de bron werken.
4. Kies op het doel **Uploaden en controleren**, vervolgens **Overstappen naar een afzonderlijke Custom App**
   en **Herstelproef uitvoeren**. Controleer het bron- en doelnummer, de versie, de woning en de aantallen
   per tabel. Meldt de proef ontbrekende gegevens, een gevuld doel of onbevestigde Zigbee2MQTT-bestanden,
   laat dan de broninstallatie in gebruik. Bevestig het getoonde doel en kies **Nieuwe app vullen**.
5. Log opnieuw in op het doel met het bestaande beheerdersaccount. De doelapp blijft in veilige modus.
   Gebruik bij onleesbare apparaatsleutels **Oude hoofdsleutel uploaden** in het herstelscherm of koppel die
   apparaten opnieuw. Wacht tot de achtergronddienst apparaten heeft gezien. Bekijk de vergelijking per
   woning en functie en controleer ontbrekende, onbekende of dubbele apparaten. Bevestig de overstap
   pas als de vergelijking klopt, de bron stil staat en de laatste gegevens zijn meegenomen.
6. **Terugvallen.** Zet het doel eerst in **Veilige modus** en stop de doelapp. Is er sinds de overstap
   nieuwe data in het doel ontstaan, maak dan vóór het stoppen een nieuwe volledige back-up van het doel.
   Bewaar die en de doelopslag. Gebruik de bron als actieve installatie zodra je die nieuwe gegevens
   hebt vergeleken en het behoud hebt bevestigd. Start de bestaande app met de bestaande opslag.

Met **Systeem › Veilige modus inschakelen** kun je apparaatbediening pauzeren. In deze stand kan de
achtergronddienst nog meten. Volg stap 3 voor de definitieve export.

### Met de opdrachtregel

```bash
docker exec -it <api-container> shc-api verify-backup /backups/shc-YYYYMMDD-HHMMSS.shcbak
```

Dit controleert alleen: versie, aantallen per tabel, Zigbee2MQTT-gegevens en of de sleutels passen bij
deze server. Echt terugzetten:

```bash
docker exec -it <api-container> shc-api maintenance on
docker exec -it <api-container> shc-api restore /backups/shc-YYYYMMDD-HHMMSS.shcbak --z2m-dir /data/zigbee2mqtt-restore
```

- `restore` weigert zolang de worker draait. Zet eerst de onderhoudsmodus aan (zoals hierboven) of stop
  de worker-container.
- Je moet `HERSTEL` typen (of `--yes` meegeven). Met `--mode new_home` werkt het als keuze B in de app.
- Daarna staat de app in **veilige modus**: bevestig in **Systeem** dat de apparaten kloppen.
- **Zigbee:** de bestanden komen in `<root>/app/zigbee2mqtt-restore/`. Zet ze terug in
  `<root>/zigbee2mqtt/` voordat Zigbee2MQTT start en voer `install.sh` opnieuw uit (eigenaar en rechten).
  Zitten ze niet in de back-up, dan moeten de sensoren opnieuw gekoppeld worden.
- **Back-up zonder geheimen en Zigbee:** staat er in de doelmap al een `secret.yaml`, dan blijft die staan
  (*secret.yaml behouden; deze back-up bevat geen wachtwoorden*). Anders komt er een `secret.yaml` zonder
  waarden. Zet het MQTT-wachtwoord er dan weer in:
  `printf 'mqtt_password: "%s"\n' "$(sudo cat <root>/secrets/mqtt_z2m_password)" | sudo tee <root>/zigbee2mqtt/secret.yaml`.

**Sleutelcontrole.** `verify-backup`, `restore`, `restore-drill` en de controle in de app melden of de
apparaatsleutels en versleutelde instellingen te ontsleutelen zijn met de hoofdsleutel van deze server.
Zo niet, dan is de back-up met een andere hoofdsleutel gemaakt. Kies **Oude hoofdsleutel uploaden**
bij **Systeem › Herstellen en verhuizen**, of koppel die apparaten opnieuw
en stel de wachtwoordzin en het push-adres opnieuw in.

### Database-dump terugzetten

Een `.shcdump` (database-dump) zet je met de hand terug. Hij bevat geen Zigbee2MQTT-gegevens; daarvoor
heb je de wekelijkse volledige export.

De volledige export en de database-dump bewaren het connectorbewijs met vorige stand en gekoppelde
opdrachten. Bij herstel van een volledige export vervalt de uitvoertermijn van de bewaarde opdrachten.
Herstel van een export wordt geweigerd als bij een oude connectoropdracht het bewijs ontbreekt of als
bestaand open herstelbewijs verloren zou gaan. Bewaar bij handmatig terugzetten eerst een geverifieerde
export van de huidige database. Gebruik voor vergelijking een afzonderlijke database als in het doel
nog apparaatherstel openstaat. `decrypt-dump` weigert een dump met een connectortabel waarvan de
rijen niet zijn opgenomen.

Scherm de app af van de Hub en andere gebruikers vóór het herstel en houd die afscherming in stand
tot en met de beheercontrole hieronder. Stop de API en worker nadat je de dump hebt ontsleuteld.
Ga bij een fout pas verder nadat het herstel is gecorrigeerd.

```bash
docker exec -it <api-container> shc-api decrypt-dump /backups/<bestand>.shcdump --out /backups/herstel.pgdump
docker stop <worker-container> <api-container>
docker exec -i <db-container> pg_restore --clean --if-exists --no-owner --single-transaction -U shc -d shc < <root>/backups/herstel.pgdump
rm <root>/backups/herstel.pgdump
```

Verwijder het ontsleutelde bestand ook als `pg_restore` faalt. Oudere dumps kunnen opgeslagen
koppelgegevens bevatten. Maak daarom direct na een geslaagd herstel de aanwezige koppeltabellen
leeg, terwijl de API en worker nog gestopt zijn. Het commando werkt ook bij een schema waarin deze
tabellen nog ontbreken.

```bash
docker exec -i <db-container> psql -X --single-transaction -v ON_ERROR_STOP=1 -U shc -d shc -f - <<'SQL'
SELECT format('TRUNCATE TABLE %I.%I', schemaname, tablename)
FROM pg_catalog.pg_tables
WHERE schemaname = 'public'
  AND tablename IN ('connector_hub_pins', 'connector_nonces', 'idempotency_keys')
\gexec
SQL
```

Start de API met toegang voor alleen de beheerder. Een dump zet de veilige modus niet aan.
Was de app gekoppeld aan een Hub, bevestig dan de actuele publieke sleutelset opnieuw in my_house.
Teruggezette opdrachten kunnen ook na herbevestiging uitsluitend als bewijs worden gelezen. Nieuwe opdrachten
vereisen een nieuw lokaal voorstel en een nieuwe goedkeuring.
Vergelijk de vingerafdruk met die van de Hub en registreer in de Hub de nieuwe bevestiging voor
deze instantie. Start de worker en open de app pas na de beheercontrole en, bij een bestaande
Hub-koppeling, na deze herbevestiging. Houd het Hub-schrijfpad afgeschermd zolang de herbevestiging
ontbreekt. Bij een installatie zonder Hub-koppeling blijft het schrijfpad uit.

### Hub-sleutelset bevestigen

1. Open als beheerder **Instellingen → Diensten** in de Hub. Kopieer daar de publieke sleutelset.
2. Open **Systeem → Koppeling met de Hub (bediening)** in my_house. Plak de set en kies **Controleren**.
3. Vergelijk de berekende vingerafdruk met die in de Hub. Typ de vingerafdruk uit de Hub over en kies **Vastleggen**.
4. Lees de vastgelegde vingerafdruk en rotatievolgorde af. Gebruik die waarden bij de bevestiging
   van deze instantie in de Hub.

Voor een ondertekende rotatie kies je **Sleutelrotatie controleren** en plak je de JSON met
rotatieverklaringen van de Hub. Vergelijk de oude en nieuwe vingerafdruk. De oude set blijft actief
tot de nieuwe set is bevestigd. Als de Hub alleen een publieke sleutelset levert, gebruik je
**Opnieuw koppelen**.

Meldt my_house dat de opgeslagen sleutelset beschadigd is, kies dan **Sleutelset herstellen**.
Bevestig met dezelfde stappen de actuele sleutelset van de vertrouwde Hub. Tot die bevestiging
weigert my_house ondertekende uitvoeringsverzoeken. Is de sleutelset intussen gewijzigd, lees dan
de actuele status en begin de controle opnieuw. Na een herstel kunnen nieuwe uitvoeringen
beginnen voor verzoeken die vanaf de eerste volledige seconde na bevestiging zijn uitgegeven.

## Herstel oefenen (aanbevolen elk kwartaal)

```bash
docker exec -it <api-container> shc-api restore-drill /backups/shc-YYYYMMDD-HHMMSS.shcbak
```

Dit zet de back-up terug in een **tijdelijke database** naast de echte (de echte database en de worker
merken er niets van) en controleert:

- of elke tabel evenveel records heeft als in de back-up;
- of de apparaatsleutels en de versleutelde instellingen (wachtwoordzin voor back-ups, push-adres) te
  ontsleutelen zijn met de hoofdsleutel die de server **nu** gebruikt.

Aan het eind staat *RESULTAAT: geslaagd* of *NIET geslaagd*. Daarna wordt de tijdelijke database
verwijderd; met `--keep` blijft hij bestaan, met `--json` krijg je het resultaat als JSON. Zorg voor
genoeg vrije schijfruimte (ongeveer de grootte van de database).

Faalt alleen de sleutelcontrole, dan is de back-up met een andere hoofdsleutel gemaakt dan deze server
gebruikt. Zoek de oude `secret_key` op en gebruik na een echt herstel `shc-api rekey --old-key-file`.
Heb je de oude sleutel niet meer, dan moet je na een herstel Hue, HomeWizard (v2) en Gree opnieuw
koppelen.

## Verhuizen

Gebruik de verhuisassistent: **Systeem › Herstellen en verhuizen**.

**A. Zelfde woning, nieuwe server** (nieuwe NAS, herinstallatie):

1. Installeer op de nieuwe server ([installatie-truenas.md](installatie-truenas.md)). Zet de
   `secret_key` van de oude server in `<root>/secrets/secret_key` (maak de map `secrets` zo nodig eerst
   aan) **voordat** je `install.sh` uitvoert: dan maakt het script geen nieuwe en zet het de rechten. Geen oude sleutel? Zie
   [Hoofdsleutel vervangen](#hoofdsleutel-vervangen).
2. Maak het beheerdersaccount aan, upload de back-up en kies **A**. De controle zegt vooraf of de
   sleutels passen.
3. Alles komt terug zoals het was. Apparaten worden herkend op serienummer, MAC of bridge-id, niet op
   IP-adres. Controleer bij stap 4 wat werkt en hef de veilige modus op.
4. Zigbee: zet de Zigbee2MQTT-gegevens terug (zit in de volledige export) voordat Zigbee2MQTT start.
5. Telefoons blijven gekoppeld als de server dezelfde adressen en sleutels heeft. Anders: opnieuw
   koppelen via **Account › Telefoons**.

**B. Nieuwe woning (verhuisd):**

1. Upload de back-up op de server in het nieuwe huis en kies **B**.
2. De woning(en) uit de back-up worden **gearchiveerd**: alleen-lezen, de historie blijft gekoppeld aan
   de oude meters en het oude huis, en hun koppelingen gaan uit.
3. Er komt een nieuwe woning bij ("<oude naam> (nieuw)") met de kamers Woonkamer en Slaapkamer en twee
   klimaatzones. **Comfortprofielen** gaan mee, en het **schema** van elke zone gaat als concept naar de
   nieuwe zone met dezelfde naam. Die zones staan uit tot je *Terug naar schema* kiest, zodat er niets
   gaat draaien voordat de airco gekoppeld en getest is. Kalibratie en het adaptieve model beginnen
   opnieuw.
4. Koppel de apparaten in het nieuwe huis (**Apparaten**, of de installatiewizard op `/installatie`).
   Een nieuw apparaat koppel je aan dezelfde rol, bijvoorbeeld *Airco totaal*; de oude binding wordt
   gesloten, niet verwijderd.
5. Functies die het nieuwe huis niet heeft (bijvoorbeeld geen tweede airco), blijven grijs met uitleg.
6. Hef de veilige modus op als alles klopt.

Zonder back-up verhuizen kan ook: **Systeem › Woningen** → oude woning archiveren en een nieuwe woning
aanmaken. Schema's en profielen gaan dan niet mee.

## Hoofdsleutel vervangen

`secrets/secret_key` (de hoofdsleutel) versleutelt de apparaatsleutels (Hue, HomeWizard API v2 en de
eigen sleutel van elke Gree-unit), de wachtwoordzin voor automatische back-ups en het push-adres.
Inlogsessies en telefoon-sleutels hangen er niet van af en blijven geldig.

**Met de oude sleutel** (aanbevolen, bijvoorbeeld na een herstel op een nieuwe NAS of als je de sleutel
wilt vervangen):

1. Bewaar de oude sleutel: `sudo mv <root>/secrets/secret_key <root>/secrets/secret_key.oud`.
2. Voer `install.sh` opnieuw uit: dat maakt een nieuwe `secret_key` met de juiste rechten. (Of zet de
   sleutel van de server waarvan je de back-up terugzette terug als `secret_key`.)
3. Herstart de app. Integraties waarvan de sleutel niet meer past, staan op *Sleutel past niet, koppel
   opnieuw*; de rest werkt door.
4. Geef de oude sleutel tijdelijk aan de API-container en zet alles om:

   ```bash
   sudo install -o 10001 -g 10001 -m 0400 <root>/secrets/secret_key.oud <root>/app/oude_sleutel
   docker exec -it <api-container> shc-api rekey --old-key-file /data/oude_sleutel --dry-run
   docker exec -it <api-container> shc-api rekey --old-key-file /data/oude_sleutel
   sudo rm <root>/app/oude_sleutel
   ```

   `--dry-run` laat zien wat er zou gebeuren zonder iets op te slaan. `rekey` versleutelt de
   apparaatsleutels, de wachtwoordzin voor back-ups en het push-adres opnieuw met de nieuwe sleutel.
   Binnen een paar minuten gebruikt de worker de koppelingen weer.
5. Bewaar de nieuwe `secret_key` in je wachtwoordmanager en maak een nieuwe back-up.

**Zonder de oude sleutel:**

- Koppel Hue, HomeWizard (API v2) en elke Gree-unit opnieuw via **Apparaten**. Opnieuw koppelen werkt de
  bestaande koppeling bij; de historie blijft.
- Stel de wachtwoordzin voor automatische back-ups opnieuw in (**Systeem › Back-ups en bewaren**). Tot
  dan staat er de melding *Automatische back-up kan niet*; die verdwijnt binnen ongeveer 10 minuten nadat
  je hem hebt ingesteld.
- Stel het push-adres opnieuw in (**Systeem › Meldingen buiten de app**). Zolang het oude adres niet te
  ontsleutelen is, toont de app *Opgeslagen push-adres onbruikbaar* en wordt het genegeerd.

## Meldingen buiten de app

Een melding in de app bereikt je niet als de app zelf stilvalt: een gestopte worker stuurt ook geen
Android-melding. Daarom kan de API (een andere container) een kort bericht naar een adres naar keuze
sturen, bijvoorbeeld een [ntfy](https://ntfy.sh)-onderwerp.

1. **Systeem › Meldingen buiten de app**: vul een ntfy-adres in (`https://ntfy.sh/<geheim-onderwerp>`
   of een eigen ntfy-server in je thuisnetwerk) of een andere webhook, en kies **Opslaan**. Het
   onderwerp is het geheim: kies een lange willekeurige naam. Het adres wordt versleuteld opgeslagen;
   daarna toont de app alleen het schema en de host.
2. Kies **Testbericht sturen** om het te controleren. **Adres verwijderen** zet het weer uit.

Je krijgt één bericht als de achtergronddienst langer dan 3 minuten geen hartslag heeft (en één als hij
terug is), en één als automatische back-ups mislukken of de laatste goede back-up ouder is dan 36 uur
of er geen is (en één als dat weer in orde is). De eerste controle is 3 minuten na het starten van de
API. Een adres met `gebruiker:wachtwoord@` wordt geweigerd.

Op een Linux-server kun je ook `SHC_NOTIFY_URL` in de omgeving of `.env` zetten; een adres in de app
gaat daar altijd voor. De TrueNAS-YAML bevat bewust geen geheimen: stel het adres daar in de app in.

## Achtergronddienst (worker)

- De worker schrijft elke 30 seconden een hartslag. Het overzicht en **Systeem** tonen hoe oud die is; na
  3 minuten zonder hartslag verschijnt *Achtergronddienst reageert niet* (en het push-bericht).
- De container heeft een healthcheck: geen hartslag in 180 seconden = *unhealthy*.
- Blijft een van de interne lussen hangen, dan stopt de worker zichzelf (waakhond) en start Docker hem
  opnieuw (`restart: unless-stopped`). In het logboek staat welke lus vastzat.
- Een koppeling waarvan de sleutel niet meer past, stopt alleen die koppeling; de rest blijft draaien.
- De Hue-eventstream schrijft één regel per verandering in het logboek (*verbonden*, *verbroken …
  opnieuw verbinden*, *weer verbonden*).
- Lampen, scènes en knoppen die op de Hue Bridge zijn verwijderd, worden na de volgende volledige
  inleesronde gearchiveerd; komen ze terug, dan worden ze weer actief.

## Bewaartermijnen

**Systeem › Back-ups en bewaren › Bewaartermijnen**: hoe lang ruwe gegevens bewaard blijven.

| Gegevens | Standaard | Minimaal |
|---|---|---|
| Vermogen (detail) | 30 dagen | 7 dagen |
| Meterstanden (ruw) | 365 dagen | 90 dagen |
| Logboek klimaatregeling | 90 dagen | 7 dagen |
| Sensormetingen (temperatuur, vochtigheid, batterij) | 400 dagen | 30 dagen |

Maximaal 3650 dagen.

Uurtotalen van energie en gas blijven altijd bewaard, en meterstanden die nog niet in uurtotalen zijn
verwerkt, worden nooit verwijderd. De standaardwaarden komen uit `SHC_RETENTION_POWER_DAYS`,
`SHC_RETENTION_COUNTER_DAYS`, `SHC_RETENTION_LOG_DAYS` en `SHC_RETENTION_SENSOR_DAYS`; wat je in de app
instelt, gaat voor.

*Sensormetingen* zijn alle ruwe metingen behalve vermogen en meterstanden: temperatuur, vochtigheid, de
doeltemperatuur en binnentemperatuur van de airco, batterij, signaalsterkte, luchtdruk en
batterijspanning. Ze blijven minstens 30 dagen bewaard (ook als `SHC_RETENTION_SENSOR_DAYS` lager is),
want het adaptieve model rekent met de laatste 14 dagen. Grafieken en experimenten die verder teruggaan
dan de ingestelde termijn hebben daar geen temperatuurgegevens meer. De worker ruimt elk uur op, in
porties, zodat de database niet lang bezig is.

## Telefoons

- Koppelen, de rol kiezen en intrekken: **Account › Telefoons** in de web-app (alleen als beheerder, met
  wachtwoord ingelogd). Een telefoon met rol *Beheren* vraagt bij het koppelen je eigen wachtwoord.
- Een telefoon-sleutel verloopt na 180 dagen; de app vernieuwt hem zelf zolang hij af en toe verbinding
  heeft.
- **Database-update 0004:** telefoons die vóór versie 0002 zijn gekoppeld en nooit een vervaldatum
  kregen, vervallen 180 dagen na hun laatste gebruik (minstens 30 dagen na de update) en hebben nu rol
  *Bedienen*. In **Account › Logboek** staat daarvan per telefoon een regel. Wil je dat zo'n telefoon weer
  beheert, koppel hem dan opnieuw met rol *Beheren*.
- Werk bij een nieuwe versie eerst de **server** bij en daarna de app: een nieuwe app herkent een oude
  server niet op het thuisadres (de tunnel blijft werken).

## Wachtwoord vergeten

```bash
docker exec -it <api-container> shc-api reset-password <gebruikersnaam>
```

Alle web-sessies van die gebruiker worden uitgelogd en zijn telefoons ingetrokken (opnieuw koppelen met
een QR-code). Een geblokkeerde gebruikersnaam (te veel mislukte pogingen) kan een andere beheerder
vrijgeven bij **Account › Gebruikers › Ontgrendelen**.

## Afhankelijkheden bijwerken (voor wie de code bijhoudt)

- De Python-pakketten van de API-image komen uitsluitend uit het lock-bestand met hashes
  `apps/api/requirements.txt`, gemaakt uit `apps/api/requirements.in`. Na het wijzigen van een
  afhankelijkheid maak je het opnieuw, vanuit `apps/api`:
  `uv pip compile requirements.in --universal --python-version 3.12 --generate-hashes --no-emit-package shc-api --no-emit-package shc-integrations -o requirements.txt`.
  De image-build en CI falen (`pip check`) als het lock-bestand niet meer bij de minimale versies past.
- Dependabot stelt wekelijks updates voor (ook voor het lock-bestand, de GitHub Actions, de images, npm
  en Gradle). Een update van een minimale versie in `integrations/` vraagt om een nieuw lock-bestand in
  dezelfde pull request.
- Elke maandag draait **Weekly audit** (`.github/workflows/audit.yml`): pip-audit, npm audit en een
  Trivy-scan van de images. Rood alleen bij de uitgebrachte image betekent: een nieuwe build heeft de
  oplossing al, breng een nieuwe versie uit.
- Een nieuwe audit-actie in de API heeft een Nederlands label nodig in `apps/web/src/i18n.ts`
  (`AUDIT_ACTION_LABEL`); een test controleert dat.
