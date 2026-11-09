# Installatie op TrueNAS SCALE 25.10

De app is één Custom App: één YAML met meerdere containers (database, API, worker, web, MQTT-broker en optioneel Zigbee2MQTT en Cloudflare Tunnel).

## 1. Dataset maken

**Datasets › Add Dataset**, bijvoorbeeld `tank/apps/smart-home`, met **Dataset Preset: Generic**. Alle gegevens komen hier: database, back-ups, broker, Zigbee. Neem de dataset mee in je TrueNAS-snapshots (die blijven op de NAS).

Wil je de back-ups ook buiten huis bewaren (replicatie naar een andere NAS of de cloud)? Maak dan ook een **child dataset** `tank/apps/smart-home/backups` (preset *Generic*) en repliceer **alleen die**. Repliceer nooit de hele dataset: in `secrets/`, `app/` en `postgres/` staan de hoofdsleutel en de database, en daarmee is de opgeslagen wachtwoordzin van de back-ups te ontsleutelen. Zie [onderhoud.md](onderhoud.md#bewaren-buiten-de-nas).

Waarom *Generic* en niet *Apps*: `install.sh` geeft elke map en elk geheim een eigen eigenaar met strikte rechten (bijvoorbeeld: alleen de database mag het databasewachtwoord lezen). Het preset *Apps* (en *SMB*) gebruikt ACL's. Die kunnen deze rechten overschrijven of blokkeren, waardoor geheimen voor iedereen leesbaar worden of een container zijn bestanden niet kan lezen. `install.sh` controleert dit en waarschuwt dan.

Heb je de dataset al met preset *Apps* gemaakt? Maak dan een nieuwe dataset met preset *Generic* en installeer opnieuw, of verwijder de ACL via **Datasets › smart-home › Permissions › Edit › Strip ACL** en voer `install.sh` opnieuw uit.

## 2. Voorbereiden (eenmalig, en opnieuw na elke update)

Open **System › Shell** en voer uit:

```bash
git clone https://github.com/lvos04/my_house.git /mnt/tank/apps/smart-home/src
```

```bash
sudo /mnt/tank/apps/smart-home/src/infra/scripts/install.sh /mnt/tank/apps/smart-home
```

Dit maakt:

- de mappen, elk met de eigenaar van de container die ze gebruikt;
- willekeurige geheimen in `secrets/`. Die map is alleen voor root. Elk geheim is alleen leesbaar voor de container die het nodig heeft (rechten 0400 of 0440);
- de broker-wachtwoorden en de toegangsregels van de MQTT-broker;
- de Zigbee2MQTT-configuratie. Die map is leesbaar voor de app, zodat de netwerksleutel en de apparatenlijst in de back-up komen.

Aan het eind controleert het script per container of die zijn eigen bestanden kan lezen: `controle: elke container kan zijn eigen bestanden lezen`. Staat daar een `LET OP`, lees dan de melding (meestal: het preset van de dataset, zie stap 1).

Opnieuw uitvoeren is veilig: bestaande geheimen, het wachtwoordbestand van de broker en de Zigbee2MQTT-configuratie blijven staan. Rechten en eigenaars worden opnieuw gezet.

Heb je een **ZBDongle-P** (CC2652)? Zet dan vóór het script `export SHC_ZIGBEE_ADAPTER=zstack`.

## 3. YAML maken

Zonder Zigbee en tunnel (aanbevolen eerste stap):

```bash
python3 /mnt/tank/apps/smart-home/src/infra/scripts/render_truenas.py --root /mnt/tank/apps/smart-home --lan-url http://192.168.1.20:8088 > /mnt/tank/apps/smart-home/smart-home.yaml
```

Vervang `192.168.1.20` door het IP-adres van je NAS. De versie van de app komt uit het bestand `VERSION` in de broncode; met `--version x.y.z` kies je zelf een versie.

Later met Zigbee en tunnel (zet eerst het tunneltoken klaar, zie [buiten-huis.md](buiten-huis.md)):

```bash
python3 /mnt/tank/apps/smart-home/src/infra/scripts/render_truenas.py --root /mnt/tank/apps/smart-home --lan-url http://192.168.1.20:8088 --remote-url https://huis.jouwdomein.nl --with zigbee,tunnel --zigbee-device /dev/serial/by-id/<jouw-dongle> > /mnt/tank/apps/smart-home/smart-home.yaml
```

De YAML bevat geen geheimen: ook het tunneltoken wordt als bestand uit `secrets/` gelezen.

**Interne netwerken.** De app maakt drie interne netwerken: `172.30.250.0/29`, `172.30.251.0/28` en `172.30.252.0/27`. Meldt TrueNAS bij het installeren iets als *Pool overlaps with other one*, dan gebruikt een andere app die adressen al. Voeg dan `--net-prefix 10.251` toe (of een ander vrij privé-bereik) en maak de YAML opnieuw.

## 4. App installeren

**Apps › Discover Apps › ⋮ › Install via YAML**. Naam: `smart-home`. Plak de inhoud van `smart-home.yaml` en klik **Save**.

De images komen van `ghcr.io/lvos04/my_house-api` en `-web`. GitHub Actions bouwt ze:

- **Release** (`release.yml`): na een merge naar `main` die het bestand `VERSION` verandert. Eerst draaien alle tests (ook de Android-tests); pas als die slagen komen de images met het versienummer en `latest`, en de GitHub-release `v<versie>`.
- **Edge** (`edge.yml`): bij elke push naar `main`, met de tag `edge`, om een versie te proberen voordat hij uitkomt.

Is het package privé, zet het dan op *public* in GitHub › Packages, of log TrueNAS in bij ghcr.io.

Alle images zijn ondertekend met [cosign](https://docs.sigstore.dev/) (keyless, vanuit de GitHub-workflow). Controleren kan op elke computer met cosign v3 (of v2 met `--new-bundle-format`):

```bash
cosign verify ghcr.io/lvos04/my_house-api:<versie> \
  --certificate-identity https://github.com/lvos04/my_house/.github/workflows/release.yml@refs/heads/main \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Hetzelfde voor `-web`. Voor `:edge` gebruik je `edge.yml@refs/heads/main`. De releasepagina noemt ook de digest van elke image.

**Geheugen.** Elke container heeft een bovengrens: database 1 GB, API 768 MB, worker 768 MB, Zigbee2MQTT 256 MB, web en tunnel elk 128 MB, broker 64 MB. In de praktijk gebruikt alles samen enkele honderden MB. Back-ups worden in stukjes gemaakt en houden het geheugengebruik vlak.

## 5. Eerste keer openen

Ga naar `http://<NAS-IP>:8088`. Zie je *De app start of wordt bijgewerkt…*, wacht dan even: de pagina probeert het elke 5 seconden opnieuw.

1. **Beheerdersaccount:** kies een gebruikersnaam en een wachtwoord van minimaal 12 tekens. Er is geen standaardwachtwoord.
2. **Woning:** zolang er geen woning is, toont elke pagina het formulier *Woning aanmaken* (naam en tijdzone).
3. **Installatiewizard** (`/installatie`, ook via **Systeem › Installatiewizard**): loopt stap voor stap door *Controles vooraf* (achtergronddienst, MQTT en Zigbee-dongle, netwerkdetectie, klok, thuisadres, back-up), *Woning*, *Adressen*, Hue, HomeWizard, Gree, Zigbee (die vier mag je overslaan), *Sensoren toewijzen*, *Meters en rollen* en *Klaar*. Je kunt hem later opnieuw openen.
4. **Adressen** (in de wizard of **Systeem › Adressen**): het adres van de app thuis, bijvoorbeeld `http://192.168.1.20:8088`, en later het adres voor onderweg (`https://…`). Zonder thuisadres kun je geen telefoon koppelen.
5. **Back-ups:** zet bij **Systeem › Back-ups en bewaren** automatische back-ups aan (wachtwoordzin, tijdstip, aantal). Zie [onderhoud.md](onderhoud.md#back-ups).
6. **Apparaten** (in de wizard of op de pagina **Apparaten**). Per fabrikant staat op **Help** (`/help`) wat lokaal werkt, wat je eerst in de app van de fabrikant moet doen en wat te doen als het niet lukt:
   1. **Hue:** zoek de bridge (of vul het IP in), druk op de link-knop, klik binnen 30 s op Koppelen.
   2. **HomeWizard:** zet in de Energy-app *Lokale API* aan, voeg P1, Energy Socket en later de kWh-meter toe op IP. Koppel de rollen: P1 → *Huis totaal*, socket → *Boiler (alleen meten)*, kWh-meter → *Airco totaal*.
   3. **Gree:** koppel beide units eerst via Gree+ aan 2,4 GHz-wifi. Zoek ze daarna in de app. Lukt binden of uitlezen niet, dan toont de app de diagnose en blijft de unit op "niet ondersteund" — dan geen bediening, wel de rest van de app.
   4. **Zigbee:** start de app met `--with zigbee`, open *Koppelen*, houd de knop van de sensor ingedrukt, wijs de sensor toe aan een kamer en aan de klimaatzone.

## Zigbee-dongle doorgeven

```bash
ls -l /dev/serial/by-id/
```

Gebruik het volledige `/dev/serial/by-id/…`-pad (blijft gelijk na herstart, anders dan `/dev/ttyUSB0`). Ontbreekt de dongle, dan start de Zigbee2MQTT-container niet en zie je dat in **Systeem** (*Zigbee – USB-dongle*, los van *Zigbee – MQTT-verbinding*). De rest van de app werkt door.

Zigbee2MQTT draait als gewone gebruiker (niet als root) en mag de dongle gebruiken via de groep `dialout`. Staat in het log `permission denied` voor `/dev/zigbee`, controleer dan met `ls -l /dev/ttyUSB* /dev/ttyACM*` dat het apparaat bij de groep `dialout` hoort.

## Ontdekken van apparaten

Containers draaien in een eigen netwerk; zoeken via broadcast/mDNS werkt daardoor niet altijd (de *Controles vooraf* in de wizard zeggen of het werkt). Vul dan het IP-adres in: dat werkt altijd. Geef de apparaten een vast IP in je router (DHCP-reservering). De app herkent apparaten op serienummer/MAC, dus een nieuw IP breekt de historie niet; een nieuw adres pas je aan bij het apparaat op **Apparaten**.

Bij koppelen accepteert de app alleen adressen in je thuisnetwerk: een privé-IP-adres of een naam op `.local`, `.lan`, `.home.arpa` en dergelijke. Loopback, publieke adressen en de eigen netwerken van de app (zie *Interne netwerken* hierboven) worden geweigerd. Moet het toch, zet het adres dan in `SHC_PAIRING_ALLOWED_HOSTS` (komma-gescheiden).

## Updaten

1. Maak een back-up (**Systeem › Back-ups › Nu maken**). Vóór een database-update maakt de app daarnaast zelf een dump in `backups/pre-migrate-….sql.gz`.
2. Haal de nieuwe versie op: `git -C /mnt/tank/apps/smart-home/src pull`.
3. Voer `install.sh` opnieuw uit (stap 2). Nieuwe versies kunnen andere rechten nodig hebben.
4. Maak de YAML opnieuw (stap 3) en werk de app bij: **Apps › smart-home › Edit**, plak de nieuwe inhoud, **Save**.

Tijdens het bijwerken toont de app *De app start of wordt bijgewerkt…* tot de API klaar is. Werk eerst de server bij en daarna de Android-app. Terugdraaien: zie [onderhoud.md](onderhoud.md#updates-en-terugdraaien).

## Android-app: eigen release-sleutel

Android installeert een update alleen als die met **dezelfde sleutel** is ondertekend als de geïnstalleerde app. Daarom ondertekent GitHub de APK met jouw eigen release-sleutel. Die sleutel staat nooit in de broncode, alleen in de geheimen van je GitHub-repository.

Zolang je deze sleutel niet hebt ingesteld, bevat een release **geen APK** (wel de server-images). De releasepagina zegt dat dan ook. De Android-tests draaien bij elke release wel, ook zonder sleutel. Eenmalig instellen kost een paar minuten:

1. Maak de sleutel op je eigen computer (met Java geïnstalleerd). Kies een sterk wachtwoord en gebruik het voor beide vragen:

   ```bash
   keytool -genkeypair -v -keystore huis-release.jks -storetype PKCS12 -alias huis -keyalg RSA -keysize 4096 -validity 10958 -dname "CN=Huis, C=NL"
   ```

2. **Bewaar** `huis-release.jks` en het wachtwoord in je wachtwoordmanager **én** op een tweede plek buiten GitHub (bijvoorbeeld een USB-stick). Ben je de sleutel kwijt, dan kun je nooit meer een update installeren zonder de app eerst te verwijderen.
3. Zet de sleutel om naar tekst: `base64 -w0 huis-release.jks > huis-release.b64` (op een Mac: `base64 -i huis-release.jks -o huis-release.b64`).
4. Ga op GitHub naar de repository › **Settings › Secrets and variables › Actions › New repository secret** en maak deze vier geheimen:

   | Naam | Waarde |
   |---|---|
   | `ANDROID_KEYSTORE_B64` | de inhoud van `huis-release.b64` |
   | `ANDROID_KEYSTORE_PASSWORD` | het wachtwoord |
   | `ANDROID_KEY_ALIAS` | `huis` |
   | `ANDROID_KEY_PASSWORD` | hetzelfde wachtwoord |

   Verwijder daarna `huis-release.b64` van je computer.
5. De volgende release is met jouw sleutel ondertekend. Op de releasepagina staat het bestand `huis-v<versie>.apk.certificate-sha256` met de vingerafdruk van het certificaat. Noteer die: elke volgende release moet dezelfde hebben.

**Eenmalig opnieuw installeren bij een sleutelwissel.** Android weigert een update met een andere sleutel. Heb je nog de testversie 0.1.0 (ondertekend met een tijdelijke sleutel) of wissel je later van sleutel:

1. Verwijder de app op je telefoon.
2. Installeer de nieuwe APK.
3. Koppel opnieuw via **Account › Telefoons** in de web-app (QR-code) en vul in de app **Meer › Cloudflare Access** opnieuw in.
4. Trek bij **Account › Telefoons** de oude koppeling van die telefoon in.

Daarna installeert elke nieuwe versie gewoon over de oude heen.

## Hoe de installatie is afgeschermd

- **Geheimen:** `secrets/` is alleen voor root (0700). Elk bestand is alleen leesbaar voor de container die het nodig heeft: `db_password` voor de database (uid 70) en de app (groep 10001), `secret_key` en `mqtt_shc_password` voor de app (uid 10001), `tunnel_token` voor `cloudflared` (uid 65532). Het wachtwoordbestand en de toegangsregels van de broker zijn 0640.
- **Netwerken:** `cloudflared` ziet alleen de webserver. De database, de broker en Zigbee2MQTT zitten in een intern netwerk zonder verbinding naar buiten; alleen de API en de worker kunnen erbij.
- **Containers:** alles draait als gewone gebruiker, met een alleen-lezen bestandssysteem, zonder extra Linux-rechten (`cap_drop: ALL`) en met `no-new-privileges`. Images hebben een vaste versie: PostgreSQL 16.15, Mosquitto 2.0.22, Zigbee2MQTT 2.14.3 en een recente `cloudflared`; de app-images zijn ondertekend met cosign.
- **Gezondheid:** database, API, broker, web en tunnel hebben een healthcheck. De worker schrijft elke 30 seconden een hartslag; na 180 seconden zonder hartslag is hij *unhealthy*, en blijft een interne lus hangen, dan stopt hij zichzelf zodat Docker hem opnieuw start.
- **Broker:** de app mag alle Zigbee-berichten lezen, maar alleen twee opdrachten sturen: nieuwe apparaten toelaten en een apparaat hernoemen.
