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
   Ook bij gelijk schema maakt de API deze export voordat zij een bestaande connectorreservering overdraagt. De export bewaart het oorspronkelijke claimtijdstip, de opdracht-ID en het lokale uitvoerrecht. Gebruik voor een proef met de oorspronkelijke worker de geverifieerde export en de softwareversie die de opdracht inplande.
4. De worker start pas als de API gezond is, dus nooit tegen een half bijgewerkte database.

**Database van een nieuwere versie.** Start een oudere versie van my_house op een database die een nieuwere versie al
heeft bijgewerkt, dan herkent de API dat aan de databaserevisie: die staat niet in de migraties van deze versie. Dat
gebeurt vóór alles: er komt geen dump, geen migratie en geen enkele schrijfactie. In plaats van steeds opnieuw te
starten, start de API in een **herstelmodus**. Elke vraag aan de API krijgt dan de uitleg *De database is van een
nieuwere my_house-versie; kies in het formulier van de Custom App de bijpassende versie of zet de snapshot van vóór de
update terug.* De status van de API (`/api/v1/health`) meldt `herstelmodus`, zodat de app niet als gezond geldt;
`/api/v1/system/recovery` geeft dezelfde uitleg met de revisie van de database en de nieuwste revisie die deze versie
kent. De worker wacht. Kies in het formulier van de Custom App weer de versie die bij de database hoort.
Gebruik voor terugval naar een oudere versie de onderstaande procedure.

**Terugdraaien.** Bereid eerst de afzonderlijke snapshotkopie voor zoals beschreven onder Back-ups. Kies daarna de vorige versie met die kopie op aparte opslag. Controleer de woning en de versleutelde koppelingen voordat je de nieuwe installatie gebruikt.

**Lokale herstelproef voor een pre-migratiedump.** Bewaar dump en controlesom in dezelfde map. `shc-api verify-migration-dump <dump> --out <nieuw.sql>` valideert de SHA-256, de gzip-integriteit en het einde van de SQL-export. Het uitvoerbestand moet nieuw zijn. Kies een ongebruikte databasenaam. Voer het aanmaken en importeren als één opdracht uit: `createdb --template=template0 herstelproef && psql -X --single-transaction -v ON_ERROR_STOP=1 -d herstelproef -f <nieuw.sql>`. Bij een bestaande naam stopt de opdracht vóór de import. Vergelijk revisie en tabelinhoud met de voorstaat en bewaar de originele geheimen voor het lezen van versleutelde gegevens. Gebruik voor oude dumps zonder opgeslagen controlesom een onafhankelijk geverifieerde snapshot of back-up.

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
  geheimen behouden. Voor herstel via de webapp heb je een volledige export nodig waarmee een beheerder kan inloggen.
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
2. Kies **Zelfde woning**, **Nieuwe woning** of **Overstappen naar een afzonderlijke Custom App**.
   De herstelproef controleert het archief in een tijdelijke database. Bij overstappen is die proef verplicht.
3. Kies **Doelback-up maken en vergelijken**. Bekijk de woningen en de inhoudsverschillen per tabel,
   ook als de aantallen gelijk zijn. Download de geverifieerde doelback-up en bewaar de wachtwoordzin
   van het gekozen archief erbij op een veilige plek. Je kunt deze kopie later via dezelfde wizard terugzetten.
4. Bevestig de vergelijking en kies **Herstellen** of **Nieuwe app vullen**. Bij gewijzigde doelgegevens
   vraagt de app om een nieuwe vergelijking. Na terugzetten staat de app in veilige modus.
   Log in met een beheerder uit de teruggezette back-up om apparaten en functies te vergelijken.
   Bij een onderbreking toont de taak of de databasevervanging is vastgelegd. Staat er **Onbekende uitkomst**,
   log dan in op de doelapp en controleer de taak en het herkomstrapport voordat je opnieuw begint.

### Overstappen van een bestaande Compose-installatie

1. Open op de bron **Systeem › Herstellen en verhuizen › Definitieve export op de bron**. Werk de
   bronapp zo nodig eerst bij zodat deze bediening beschikbaar is. Bewaar de broninstallatie met eigen opslag
   en hoofdsleutel voor terugval.
2. Kies **Bron pauzeren voor overstap**. De app blokkeert wijzigingen in de database, ook binnenkomende
   metingen. Kijkers kunnen blijven lezen en opnieuw aanmelden. Het scherm toont het laatste meetrecord,
   het aantal metingen en de vervaltijd. De pauze overleeft een herstart. De Zigbee-opslag moet leeg zijn
   en gedurende de overstap tegen schrijven beschermd zijn. Voor bestaande Zigbee-bestanden of een
   gekoppelde Zigbee-integratie ontbreekt een beproefd webherstel. Gebruik in dat geval de broninstallatie verder.
   Als de Zigbee-dienst in een lege Compose-map kan schrijven, weigert de app de pauze. Controleer in
   het opslagbeheer het eigendom en de rechten. Een andere eigenaar dan de Zigbee-dienst en alleen
   lees- en zoekrechten voor groep en overige gebruikers beschermen de lege map. Bewaar map en inhoud.
3. Tijdens de pauze kun je **Tussenexport maken** kiezen. De kijker kan zich daarna opnieuw aanmelden.
   Kies voor de overstap **Bronregistratie afsluiten en exporteren**, terwijl je als beheerder aangemeld blijft.
   Deze actie wacht op lopende aanmeldingen, sluit nieuwe aanmeldingen en neemt het volledige logboek mee.
   Download de **Definitieve export**. Bestaande leessessies kunnen blijven lezen totdat je de bronapp stopt.
   Zonder definitieve export kun je de pauze meteen opheffen. Na een definitieve export kun je de bron
   hervatten zodra het bewijs vervalt, één uur na het begin van de pauze.
4. Installeer het doel als afzonderlijke standaard Custom App met eigen opslag. Maak alleen de tijdelijke
   beheerder aan. Upload de definitieve export en kies **Overstappen naar een afzonderlijke Custom App**.
   Voer de herstelproef uit. Een doel met gebruikershistorie, waaronder gelezen meldingen of beheerhandelingen,
   wordt geweigerd. Vergelijk de bron en het doel, bewaar de doelback-up en bevestig de vervanging.
5. Log opnieuw in op het doel. Lees zo nodig de oude hoofdsleutel in via **Oude hoofdsleutel uploaden**.
   Controleer woningen, apparaten, functies en de inhoudsvergelijking per gegevenssoort. Een gewijzigde
   knopactie of ontbrekende meting houdt de vrijgave tegen. De doelretentie wacht tot de vrijgave.
   Bij een open overstap vind je de oorspronkelijke kopie onder **Doelback-up van deze taak downloaden**.
   Je kunt die via **Zelfde woning** terugzetten na een nieuwe doelback-up en vergelijking.
   De actuele vergelijking en het bronbewijs moeten geldig zijn wanneer je **Overstap bevestigen** kiest.
6. Voor terugval zet je het doel in veilige modus. Bewaar bij nieuwe doelgegevens eerst een volledige export
   en de doelopslag. Stop de doelapp. Vergelijk de wijzigingen en bevestig het behoud voordat je op de bron
   verder werkt. Na de vervaltijd kies je op de bron **Bron hervatten**.

### Database-dump terugzetten

Een `.shcdump` (database-dump) zet je met de hand terug. Hij bevat geen Zigbee2MQTT-gegevens; daarvoor
heb je de wekelijkse volledige export.

De volledige export en de database-dump bewaren het connectorbewijs met vorige stand en gekoppelde
opdrachten. Bij herstel van een volledige export vervalt de uitvoertermijn van de bewaarde opdrachten.
Herstel van een export wordt geweigerd als bij een oude connectoropdracht het bewijs ontbreekt of als
bestaand open herstelbewijs verloren zou gaan. Bewaar bij handmatig terugzetten eerst een geverifieerde
export van de huidige database. Gebruik voor vergelijking een afzonderlijke database als in het doel
nog apparaatherstel openstaat. Bij het maken en ontsleutelen van een database-dump controleert de
app de vorige stand en herstelstatus van elke relevante connectoropdracht in een tijdelijke
PostgreSQL-database. Hiervoor heeft de databaserol het recht nodig om een database aan te maken.
De app ruimt de controledatabase na afloop op. Een eerder gecontroleerd uitvoerbestand blijft bij een fout beschikbaar.
Van een afgehandeld opdrachtpaar uit de oudste connectorversie blijven de toen gemeten voeding, modus en
doeltemperatuur als historie beschikbaar. Evidence vermeldt `readback_incomplete` voor de ontbrekende bedienvelden.

Bij een software- of schemawissel blijft een ongeclaimde connectoropdracht met haar ID, meetvelden
en de reden `versiewissel` in de historie. Bij een ingeplande rollback verschijnt **Handmatige controle
nodig** in de meldingen van de woning. Controleer de fysieke stand en de oorspronkelijke preview
voordat je verder bedient. Een eerder geclaimde opdracht met een onzekere uitkomst kan een wissel
tegenhouden. Handel die op de oorspronkelijke installatie af en controleer de teruglezing.

Gebruik voor een oudere SQL-export de huidige `verify-migration-dump` bij het schrijven van de
import-SQL. De import sluit de connectorwachtrij af binnen dezelfde transactie. Gebruik bij een
terugval met een bestaande opslagsnapshot een afzonderlijke offline kopie. Laat API en worker uit,
richt de huidige `shc-api prepare-connector-fallback` op de database van de kopie en wacht op
exitcode 0 voordat je de bijpassende oudere applicatie start. Het schema en de oorspronkelijke
opslagkopie blijven beschikbaar voor vergelijking.

Kies voor de dump een nieuwe databasenaam en gebruik de versleutelde dump rechtstreeks:

```bash
docker exec -it <api-container> shc-api restore-dump /backups/<bestand>.shcdump --new-database herstelproef
```

De opdracht controleert de versleutelde dump en importeert deze in een nieuw aangemaakte, lege database.
Ze ruimt lokale koppelgegevens uit oudere dumps op voordat ze de database onder de gekozen naam publiceert.
Bij een bezette naam stopt de opdracht. De oorspronkelijke installatie blijft beschikbaar.
Verbind voor de controle alleen een afzonderlijke, afgeschermde testinstallatie met de nieuwe database.
Laat de worker uit totdat de beheerder de vergelijking en het terugvalpad heeft gecontroleerd.
Start de afzonderlijke API met toegang voor alleen de beheerder. Een dump zet de veilige modus niet aan.
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

**A. Zelfde woning herstellen**

Kies **Zelfde woning** voor herstel op dezelfde installatie. Volg de stappen onder **In de app**.
Gebruik voor een overstap naar een nieuwe server het afzonderlijke doel en de definitieve bronexport uit
**Overstappen van een bestaande Compose-installatie**. De app toont welke apparaatsleutels opnieuw
moeten worden ingelezen. Voor Zigbee2MQTT-bestanden ontbreekt een beproefde terugzetweg in de webapp.

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

Een ingelogde beheerder kan bij **Account › Gebruikers** het wachtwoord van een andere gebruiker opnieuw
instellen of diens account ontgrendelen. Na een wachtwoordreset vervallen de bestaande sessies en
telefoonkoppelingen van die gebruiker. Je eigen wachtwoord wijzig je bij **Account › Wachtwoord wijzigen**
met je huidige wachtwoord. Zonder toegankelijk beheerdersaccount is er geen zelfherstel via de webapp.

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
