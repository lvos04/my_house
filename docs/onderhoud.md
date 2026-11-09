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

1. Maak eerst een back-up: **Systeem › Back-ups en bewaren › Nu een back-up maken**.
2. Werk bij:
   - **TrueNAS:** `git -C <root>/src pull`, voer `install.sh` opnieuw uit, maak de YAML opnieuw en plak
     hem bij **Apps › smart-home › Edit** (zie [installatie-truenas.md](installatie-truenas.md#updaten)).
   - **Linux:** `SHC_ROOT=<root> SHC_VERSION=<versie> docker compose -f infra/compose/compose.yaml up -d`.
3. Bij het starten werkt de API de database bij (migraties). Heeft de database een oudere versie, dan
   maakt de API daar eerst **automatisch een dump** van: `<root>/backups/pre-migrate-<revisie>-<tijd>.sql.gz`.
   Lukt die dump niet, dan wordt de database **niet** bijgewerkt en start de API niet; het logboek van
   de API zegt dan *Database-update geweigerd* met de reden. Elke migratie draait in één transactie:
   mislukt hij, dan blijft de database zoals hij was.
4. De worker start pas als de API gezond is, dus nooit tegen een half bijgewerkte database.

**Terugdraaien.** Zet de vorige versie terug (YAML met `--version <oude versie>`, of `SHC_VERSION`).
Is er inmiddels een migratie uitgevoerd, dan kent de oude versie het nieuwe schema niet. Zet dan de
automatische dump van vóór de update terug, in een lege database:

```bash
docker stop <worker-container> <api-container>
docker exec <db-container> psql -U shc -d postgres -c 'DROP DATABASE shc WITH (FORCE)'
docker exec <db-container> psql -U shc -d postgres -c 'CREATE DATABASE shc'
gunzip -c <root>/backups/pre-migrate-<revisie>-<tijd>.sql.gz | docker exec -i <db-container> psql -q -U shc -d shc
```

Start daarna de oude versie. Kies de dump met de revisie die bij de oude versie hoort (het nummer in de
bestandsnaam, bijvoorbeeld `0003_followups`). Alles wat na de update is gemeten, ben je dan kwijt.

De dumps van vóór een update worden niet automatisch opgeruimd. Verwijder oude zelf als je ze niet
meer nodig hebt.

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
- **Zonder geheimen** (geredigeerd): geen apparaatsleutels, geen wachtwoord-hashes, en in
  `zigbee2mqtt/secret.yaml` staan de sleutels zonder waarden. Na terugzetten is
  `shc-api reset-password` nodig en moet je apparaten opnieuw koppelen.
- **Versleuteling:** AES-256-GCM in blokken van 1 MB, met een sleutel uit je wachtwoordzin (scrypt).
  Elk bestand in het archief heeft een SHA-256-controlegetal. Een beschadigd of aangepast bestand wordt
  geweigerd.

### Bewaren buiten de NAS

- Bewaar de **wachtwoordzin** en `secrets/secret_key` in je wachtwoordmanager, buiten de NAS. Zonder
  `secret_key` zijn de apparaatsleutels in de back-up onleesbaar: dan moet je Hue, HomeWizard (API v2) en
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
2. **Kiezen:** *A · Zelfde woning, nieuwe server* of *B · Nieuwe woning (verhuisd)*, zie
   [Verhuizen](#verhuizen).
3. **Terugzetten.** De achtergronddienst pauzeert (onderhoudsmodus) en alles wordt in één keer
   vervangen. Daarna sta je in de **veilige modus**: er gaat geen enkel commando naar een apparaat. Je
   wordt uitgelogd (inlogsessies zitten niet in een back-up); log opnieuw in.
4. **Vergelijken: wat werkt er?** Per apparaat en per functie (rollen, airco en sensor per zone,
   lichtgroepen): *Werkt*, *Gedeeltelijk*, *Niet beschikbaar* of *Nieuw gevonden*, met een suggestie.
   Werkt alles zoals verwacht, kies dan **Veilige modus opheffen**.

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
Zo niet, dan is de back-up met een andere `secret_key` gemaakt. Zet de oude sleutel om met
`shc-api rekey` (zie [Hoofdsleutel vervangen](#hoofdsleutel-vervangen)), of koppel die apparaten opnieuw
en stel de wachtwoordzin en het push-adres opnieuw in.

### Database-dump terugzetten

Een `.shcdump` (database-dump) zet je met de hand terug. Hij bevat geen Zigbee2MQTT-gegevens; daarvoor
heb je de wekelijkse volledige export.

```bash
docker exec -it <api-container> shc-api decrypt-dump /backups/<bestand>.shcdump --out /backups/herstel.pgdump
docker stop <worker-container> <api-container>
docker exec -i <db-container> pg_restore --clean --if-exists --no-owner -U shc -d shc < <root>/backups/herstel.pgdump
rm <root>/backups/herstel.pgdump
docker start <api-container> <worker-container>
```

Verwijder het ontsleutelde bestand meteen: het is niet versleuteld. Een dump terugzetten zet de veilige
modus niet aan.

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

Maximaal 3650 dagen.

Uurtotalen van energie en gas blijven altijd bewaard, en meterstanden die nog niet in uurtotalen zijn
verwerkt, worden nooit verwijderd. De standaardwaarden komen uit `SHC_RETENTION_POWER_DAYS`,
`SHC_RETENTION_COUNTER_DAYS` en `SHC_RETENTION_LOG_DAYS`; wat je in de app instelt, gaat voor.
Temperatuur, vochtigheid en de doeltemperatuur van de airco worden niet automatisch opgeruimd.

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
