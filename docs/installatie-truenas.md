# Installatie op TrueNAS SCALE 25.10

De app is één Custom App: één YAML met meerdere containers (database, API, worker, web, MQTT-broker en optioneel Zigbee2MQTT en Cloudflare Tunnel).

## 1. Dataset maken

**Datasets › Add Dataset**, bijvoorbeeld `tank/apps/smart-home` (preset *Apps*). Alle gegevens komen hier: database, back-ups, broker, Zigbee. Neem de dataset mee in je TrueNAS-snapshots.

## 2. Voorbereiden (eenmalig)

Open **System › Shell** en voer uit:

```bash
git clone https://github.com/lvos04/my_house.git /mnt/tank/apps/smart-home/src
```

```bash
sudo /mnt/tank/apps/smart-home/src/infra/scripts/install.sh /mnt/tank/apps/smart-home
```

Dit maakt mappen met de juiste eigenaar, willekeurige geheimen (`secrets/`), de broker-wachtwoorden en de Zigbee2MQTT-configuratie. Opnieuw uitvoeren is veilig: bestaande geheimen blijven staan.

Heb je een **ZBDongle-P** (CC2652)? Zet dan vóór het script `export SHC_ZIGBEE_ADAPTER=zstack`.

## 3. YAML maken

Zonder Zigbee en tunnel (aanbevolen eerste stap):

```bash
python3 /mnt/tank/apps/smart-home/src/infra/scripts/render_truenas.py --root /mnt/tank/apps/smart-home --lan-url http://192.168.1.20:8088 > /mnt/tank/apps/smart-home/smart-home.yaml
```

Vervang `192.168.1.20` door het IP-adres van je NAS. Later met Zigbee en tunnel:

```bash
python3 /mnt/tank/apps/smart-home/src/infra/scripts/render_truenas.py --root /mnt/tank/apps/smart-home --lan-url http://192.168.1.20:8088 --remote-url https://huis.jouwdomein.nl --with zigbee,tunnel --zigbee-device /dev/serial/by-id/<jouw-dongle> --tunnel-token-file /mnt/tank/apps/smart-home/secrets/tunnel_token > /mnt/tank/apps/smart-home/smart-home.yaml
```

Let op: met `--with tunnel` staat het tunneltoken in de YAML. Deel dat bestand niet.

## 4. App installeren

**Apps › Discover Apps › ⋮ › Install via YAML**. Naam: `smart-home`. Plak de inhoud van `smart-home.yaml` en klik **Save**.

De images komen van `ghcr.io/lvos04/my_house-api` en `-web`. Die worden door GitHub Actions gebouwd bij een versietag (`v0.1.0`). Is het package privé, zet het dan op *public* in GitHub › Packages, of log TrueNAS in bij ghcr.io.

## 5. Eerste keer openen

Ga naar `http://<NAS-IP>:8088`. Maak het beheerdersaccount (minimaal 12 tekens). Daarna in **Apparaten**:

1. **Hue:** zoek de bridge (of vul het IP in), druk op de link-knop, klik binnen 30 s op Koppelen.
2. **HomeWizard:** zet in de Energy-app *Lokale API* aan, voeg P1, Energy Socket en later de kWh-meter toe op IP. Koppel de rollen: P1 → *Huis totaal*, socket → *Boiler (alleen meten)*, kWh-meter → *Airco totaal*.
3. **Gree:** koppel beide units eerst via Gree+ aan 2,4 GHz-wifi. Zoek ze daarna in de app. Lukt binden of uitlezen niet, dan toont de app de diagnose en blijft de unit op "niet ondersteund" — dan geen bediening, wel de rest van de app.
4. **Zigbee:** start de app met `--with zigbee`, open *Koppelen*, houd de knop van de sensor ingedrukt, wijs de sensor toe aan een kamer en aan de klimaatzone.

## Zigbee-dongle doorgeven

```bash
ls -l /dev/serial/by-id/
```

Gebruik het volledige `/dev/serial/by-id/…`-pad (blijft gelijk na herstart, anders dan `/dev/ttyUSB0`). Ontbreekt de dongle, dan start de Zigbee2MQTT-container niet en zie je dat in **Systeem**. De rest van de app werkt door.

## Ontdekken van apparaten

Containers draaien in een eigen netwerk; zoeken via broadcast/mDNS werkt daardoor niet altijd. Vul dan het IP-adres in: dat werkt altijd. Geef de apparaten een vast IP in je router (DHCP-reservering). De app herkent apparaten op serienummer/MAC, dus een nieuw IP breekt de historie niet.
