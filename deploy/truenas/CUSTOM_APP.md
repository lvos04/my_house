# Smart Home Control installeren als standaard Custom App

Open in TrueNAS **Apps › Discover Apps › Custom App**. Eén image bevat de webinterface, API, worker, database en broker. Voor de eerste installatie heb je een lege, eigen dataset, een vrije hostpoort en de vaste tag en digest van de gepubliceerde alles-in-één-image nodig.

Gebruik de GitHub-release voor de alles-in-één-image (vanaf `v0.3.0`). Download de bijlage `digests.json` en controleer dat `app_id` gelijk is aan `smart-home` en `image` aan `ghcr.io/lvos04/my_house`. Zodra het GHCR-pakket publiek is, kan TrueNAS het zonder registry-login ophalen. Wacht met installeren zolang de bijlage ontbreekt of het manifest nog de tagplaatshouder en `digest: null` bevat (BLOK-06). [GitHub-releases](https://github.com/lvos04/my_house/releases) · [Manifest van my_house](../manifest/my_house.manifest.yaml)

## Naam, image en container

| Veld in het formulier | Wat je invult |
|---|---|
| Application Name | `smart-home`. |
| Image › Repository | `ghcr.io/lvos04/my_house`. |
| Image › Tag | Vul `<tag>@<digest>` in met de velden uit `digests.json`, voor deze release in de vorm `0.3.0@sha256:<64 hextekens>`. Accepteert het formulier alleen een losse tag, gebruik dan de exacte versietag en controleer de opgehaalde digest vóór ingebruikname. |
| Image › Pull Policy | Kies *Pull the image if it is not already present on host*. |
| Timezone | Kies de tijdzone van je woning. |
| Entrypoint en Command | Gebruik als Entrypoint `/opt/venv/bin/python`. Voeg als drie afzonderlijke argumenten `-IBu`, `-m` en `shc_allinone.startpad` toe. Deze eerste start toont alleen de bij deze image horende modulenaam en sluit af. |
| Environment Variables | Laat leeg bij de basisinstallatie. De app maakt zelf haar sleutels op de eigen dataset. Vul desgewenst `HUB_URL` in als webadres voor de link ‘Terug naar de Hub’. Laat `HUB_ENROLLMENT_TOKEN` en `HUB_KEY_FINGERPRINT` weg. Deze versie ondersteunt geen automatische Hub-koppeling via het formulier. |
| Restart Policy | Kies voor het opvragen van de modulenaam **No**. Kies na het vastleggen van de naam **Unless Stopped**. |
| Disable Builtin Healthcheck, TTY en Stdin | Laat alle drie uit. |

### Zigbee-stick (alleen als je er een hebt)

Zonder stick sla je **Devices** over. Zigbee blijft dan uit en de kerndiensten kunnen normaal starten.

1. Kies in de apparatenlijst van het TrueNAS-formulier de seriële coördinator van jouw stick. Neem het getoonde **Host Device** over. De vorm is `/dev/serial/by-id/<NAAM-VAN-JE-STICK>`. Vul bij **Container Device** `/dev/zigbee` in.
2. De standaardadapter is `ember`. Vereist de stick een andere adapter, zet bij **Environment Variables** vóór het eerste gebruik `SHC_ZIGBEE_ADAPTER` op het type van de stick. Andere ondersteunde waarden zijn `zstack`, `deconz`, `zigate` en `zboss`. Bestaande Zigbee-configuratie blijft bewaard.
3. De diensten draaien als gebruiker 568. Voor Zigbee moet deze gebruiker de stick kunnen lezen en schrijven en moet de kernel de Zigbee-procesrechten kunnen begrenzen. Ontbreekt de stick in het formulier of blijft de donglecheck uit, laat Zigbee uit en controleer de apparaatkeuze, rechten en diagnose. De gewone healthcheck meet de kerndiensten.

Het manifest laat de apparatenlijst leeg omdat de standaard alleen vaste hostapparaten kan vastleggen (Z-17, Z-21). Apparaatdoorgifte op jouw TrueNAS-versie (D01) is **NIET GEVALIDEERD (echte NAS)**. Toegang tot de stick en de werking van een echte sensor (D13) zijn **NIET GEVALIDEERD (hardware)**.

## Beveiliging, netwerk en opslag

| Veld in het formulier | Wat je invult |
|---|---|
| Privileged en Capabilities | Laat Privileged uit en de lijst met extra capabilities leeg. |
| Custom User | Laat uit voor de standaardstart van de image. De bewaker bereidt de eigen opslag voor en laat de diensten als gebruiker en groep 568 draaien. |
| Host Network en Ports | Laat Host Network uit. Voeg één TCP-poort toe met **Container Port** `8080` en **Host Port** `8088` als die vrij is (D03). Kies bij een conflict een vrije hostpoort. PostgreSQL en MQTT blijven intern. |
| Networks en Custom DNS | Laat de standaardinstellingen staan. |
| Portals | Naam `Smart Home Control`, schema `http`, gekozen hostpoort en pad `/`. |
| Storage › Add › Host Path | Kies in de datasetkiezer je eigen, voor de eerste installatie lege dataset, bijvoorbeeld `/mnt/<POOL>/<DATASET>` (D02). Vul bij **Mount Path** `/appdata` in en laat **Read Only** uit. Hier staan onder andere de database, sleutels en back-ups. |
| Resources › CPUs / Memory | Laat de CPU- en geheugenlimieten uit, zoals in het manifest. |
| Labels en GPU | Laat eigen labels leeg en de GPU uit. |

## Opslaan en controleren

1. Sla het formulier op. Lees in het logboek de ene naam die begint met `shc_allinone.start_`. Open **Edit**, vervang het laatste argument `shc_allinone.startpad` door die volledige naam, zet **Restart Policy** op **Unless Stopped** en sla opnieuw op. Leg de modulenaam bij de tag en digest vast. Zo blijft de worker aan de bedoelde softwareversie gebonden.
2. Vergelijk de opgehaalde digest voor deze repository met het releasebewijs (teruglezing via `app.image.query`, veld `repo_digests`). Wacht bij een ontbrekende of afwijkende digest met ingebruikname. Controleer de ingebouwde healthcheck en open de portal. De HTTP-monitor verwacht op `/readyz` status 200, met een time-out van 8 seconden en een interval van 60 seconden. Bij een fout open je het opstartlog.
3. Maak op het vertrouwde netwerk direct de eerste beheerder en woning aan. Controleer dat je opnieuw kunt aanmelden. Open **Systeem › Installatiewizard** en stel onder **Systeem › Back-ups en bewaren** de volledige versleutelde back-up in. Leg met de beheerder vast hoeveel gegevensverlies (RPO) en hersteltijd (RTO) aanvaardbaar zijn (D16).

## Updaten en terugdraaien

Bewaar de huidige tag, digest en startmodule. Controleer de volledige back-up, stop de app en maak in TrueNAS een snapshot van de hele dataset. Kies bij **Edit** de nieuwe vaste versie met digest. Vraag voor die image opnieuw de startmodule op en leg die vast zoals hierboven. Controleer de digest, gezondheid en aanmelding voordat je verdergaat.

Voor terugdraaien hoort bij de vorige versie een **schrijfbare kloon van de snapshot van vóór de update**. Houd beide workers gestopt. Bereid de kloon met de huidige image offline voor. Pas na de bevestigde eindmelding en exitcode 0 start je de bijpassende vorige versie via het formulier van de afzonderlijke terugvalapp, met het vrijgegeven terugval-ID en het alleen-lezen startbestand. De oorspronkelijke dataset blijft beschikbaar. [Offline terugvalkopie voorbereiden](../../docs/onderhoud.md#offline-terugvalkopie-voorbereiden)
