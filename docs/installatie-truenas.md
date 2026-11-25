# Installatie op TrueNAS SCALE

Gebruik **Apps › Discover Apps › Custom App**. De standaard Custom App met één alles-in-één-image is de enige installatieweg. Wacht op de eerste publicatie van deze image met vaste versietag en registerdigest (BLOK-06). Voorbereiden kan met de [formulierhandleiding](../deploy/truenas/CUSTOM_APP.md).

## Opslag en formulier

Kies voor de eerste installatie een lege dataset voor `/appdata`. De voorgestelde hostpoort is 8088. Kies een vrije poort en leg de startmodule vast die de image voor deze versie in het logboek toont. De formulierhandleiding geeft de invulvolgorde, de optionele Zigbee-stick en de controles vóór ingebruikname. [Velden van de standaard Custom App](../deploy/truenas/CUSTOM_APP.md)

## Updaten en terugdraaien

Maak vóór de update een volledige, gecontroleerde back-up. Stop de app en maak een snapshot van de gehele dataset, inclusief `secrets` en de Zigbee-map. Kies de nieuwe vaste tag in het formulier en leg tegelijk de bijbehorende startmodule uit **Opslag en formulier** vast, ook bij een bestaande installatie. Laat deze configuratie bij een terugval staan en gebruik voor de kloon de afzonderlijke terugvalapp. De bewaker controleert de pre-migratiedump voordat hij de transactie met de schemawijzigingen start.

Bij een ontbrekende sleutel of onherkende indeling stopt de app. Bewaar de dataset en de snapshot. Maak op aparte opslag een schrijfbare kloon van de snapshot. Laat de oude app gestopt. Kies voor die kloon in het formulier de **huidige** image en het eenmalige startprogramma voor terugval. Dat voert intern `shc-api prepare-connector-fallback` uit. De stappen voor het formulier staan onder [Offline terugvalkopie voorbereiden](onderhoud.md#offline-terugvalkopie-voorbereiden).

Kies pas na de eindmelding **Terugvalkopie voorbereid. PostgreSQL is gestopt.** en exitcode 0 de vaste tag die bij de snapshot hoort. Stel vóór het aankoppelen van de kloon het alleen-lezen startbestand, het argument `terugval-app` en `SHC_TERUGVAL_ID` in zoals beschreven in de onderhoudsprocedure. Laat deze instellingen bij iedere start staan. Het startprogramma controleert de vrijgave op de aangekoppelde database voordat de historische diensten starten. Controleer aanmelding, woning, koppelingen, beheermeldingen en back-up.

## Overstap van een bestaande installatie

Gebruik een aparte dataset voor de Custom App. De vorige Compose-installatie heeft een andere database-indeling, andere eigenaars en een andere naam voor de hoofdsleutel. Bewaar die installatie met haar geheimen en snapshot. Wil je de vorige software opnieuw starten, volg dan eerst [Offline terugvalkopie voorbereiden](onderhoud.md#offline-terugvalkopie-voorbereiden) voor de Compose-database. De stappen voor de bronpauze en de gegevensvergelijking staan onder [Overstappen van een bestaande Compose-installatie](onderhoud.md#overstappen-van-een-bestaande-compose-installatie).

Installatie op een echte NAS en toegang tot een fysieke dongle zijn nog te toetsen (D01 en D13).

## Alleen voor ontwikkeling

De lokale proefstack gebruikt `deploy/dev/compose.yaml` en `.env.example` met eigen testgegevens. De scripts `install.sh` en `render_truenas.py` onder `infra/scripts` en de historische procedure ‘Install via YAML’ horen bij die ontwikkelproeven.

### Android-app: eigen release-sleutel

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
