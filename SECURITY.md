# Beveiliging

## Uitgangspunten

- **LAN-first.** Geen poorten open in de router, geen analytics, geen verplichte cloud. Toegang van buiten alleen via de optionele Cloudflare Tunnel, liefst met Cloudflare Access ervoor.
- **Geen standaardwachtwoord.** Het eerste account maak je zelf aan (minimaal 12 tekens), daarna is `/auth/setup` dicht.
- **Wachtwoorden:** argon2id, minimaal 12 tekens. Na 5 mislukte pogingen op één gebruikersnaam is die naam 1 minuut geblokkeerd, bij elke volgende fout dubbel zo lang (tot 15 minuten). Per IP-adres maximaal 20 mislukte pogingen per 5 minuten, en nginx laat op inloggen, eerste start en koppelen maximaal 10 verzoeken per minuut per IP-adres door. Een wachtwoordwijziging logt je andere sessies uit en trekt (tenzij je dat uitzet) je telefoons in.
- **Web-sessies:** cookie `HttpOnly`, `SameSite=Strict`, `Secure` als het verzoek via https binnenkwam, plus een CSRF-token op elke wijziging. Een sessie verloopt na 14 dagen. De WebSocket accepteert de cookie alleen als de Origin (inclusief poort) overeenkomt.
- **Telefoons:** eigen sleutel per toestel via een eenmalige QR-code (10 minuten geldig). Op de server staat alleen een hash, op de telefoon is hij versleuteld (Android Keystore). De sleutel verloopt na 180 dagen; de app vernieuwt hem zelf. Intrekbaar per telefoon; ook de open live-verbinding gaat dan meteen dicht.
- **Rollen:** gebruikers zijn `admin` of `viewer` (alleen kijken). Een telefoon krijgt bij het koppelen een eigen rol: *Bedienen* (standaard), *Alleen kijken* of *Beheren*; voor *Beheren* vraagt de app het wachtwoord van de beheerder. Gebruikers, telefoons en koppelcodes, back-ups en herstellen, adressen en het push-adres kunnen **alleen** in de web-app na inloggen met wachtwoord, nooit met een telefoon-sleutel. Een gestolen telefoon kan dus geen nieuwe beheerder, koppelcode of back-up maken.
- **Serverherkenning (Android):** voordat de app zijn sleutel naar een adres stuurt, beantwoordt de server een controlevraag (HMAC over een nieuw willekeurig getal, met de hash van de sleutel). Een ander apparaat op hetzelfde IP-adres krijgt de sleutel dus niet.
- **Logboek:** elke geslaagde wijziging komt in het auditlog (wie, wat, wanneer; nooit de inhoud van het verzoek), plus inloggen, mislukte pogingen, blokkades, koppelen en intrekken. Te zien bij **Account › Logboek**.
- **Geheimen:** apparaatsleutels, de wachtwoordzin voor automatische back-ups en het push-adres staan versleuteld in de database (AES-256-GCM, sleutel uit `SHC_SECRET_KEY_FILE`). Database- en brokerwachtwoorden zijn Docker-secrets; `secrets/` is alleen voor root en elk bestand is alleen leesbaar voor de container die het nodig heeft (0400/0440). Het tunneltoken wordt uit een bestand gelezen en staat niet in de YAML. Geheimen komen nooit in logs, in de frontend of in een API-antwoord.
- **Back-ups:** versleuteld met je wachtwoordzin (scrypt + AES-256-GCM, met controlegetallen). Een back-up *zonder geheimen* bevat geen apparaatsleutels, wachtwoord-hashes, telefoon-sleutel-hashes, push-adres of Zigbee2MQTT-wachtwoorden. Inlogsessies zitten nooit in een back-up.
- **Apparaten met eigen certificaat** (Hue, HomeWizard v2): het certificaat wordt bij het koppelen vastgelegd en tijdens de TLS-handshake gecontroleerd, voordat er een sleutel of token wordt verstuurd. Een ander certificaat wordt geweigerd.
- **Adressen bij koppelen:** alleen apparaten in het thuisnetwerk; loopback, publieke adressen en de eigen netwerken van de app worden geweigerd.
- **Containers:** vaste image-versies, niet-root, alleen-lezen bestandssysteem, `cap_drop: ALL`, `no-new-privileges`, geheugenlimieten. Drie netwerken: de tunnel ziet alleen de webserver; database, broker en Zigbee2MQTT zitten in een intern netwerk zonder route naar buiten. De MQTT-broker heeft per gebruiker een ACL.
- **Webserver en API:** Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff`, HSTS via https; verzoeken groter dan 256 kB worden geweigerd (uploads voor herstel tot 2 GB). De API-documentatie (`/api/v1/docs`) is alleen voor ingelogde beheerders.
- **Boiler:** het schakelen van de boiler-stekker is geblokkeerd in de adapter, in de API en in de worker vlak voor verzenden. Dat geldt voor alle routes, rollen, knoppen en de klimaatregelaar. De HomeWizard-app zelf kan de stekker nog wel schakelen; zet daar eventueel de schakelaarvergrendeling aan.
- **Na herstel of verhuizing:** veilige modus, geen apparaatcommando's tot jij bevestigt.

## Software en releases

- Python-pakketten komen uit een lock-bestand met hashes (`apps/api/requirements.txt`), npm uit `package-lock.json`, en de Gradle-wrapper heeft een vaste checksum.
- Dependabot stelt wekelijks updates voor (Python, npm, Gradle, GitHub Actions, images). Elke maandag draait een audit: pip-audit, npm audit en Trivy op de images. Elke build scant de images ook (Trivy, faalt op kritieke lekken met een oplossing).
- Release- en edge-images worden met cosign ondertekend (keyless, vanuit GitHub Actions) en krijgen provenance en een SBOM. Een release komt pas uit als alle tests slagen. Controleren: zie [installatie-truenas.md](docs/installatie-truenas.md#4-app-installeren).
- De Android-APK wordt ondertekend met jouw eigen release-sleutel uit de GitHub-geheimen van de repository; die sleutel staat nooit in de repository.

## Kwetsbaarheid melden

Open een privé *security advisory* in deze GitHub-repository. Niet als gewoon issue.

## Bekende beperkingen

- Lokale apparaat-API's (Gree, HomeWizard v1) gebruiken geen of zwakke versleuteling. Iedereen in je wifi kan ze in principe bedienen. Zet IoT-apparaten bij voorkeur in een apart VLAN of gastnetwerk dat de NAS wel bereikt.
- Thuis via gewone `http`: de serverherkenning voorkomt dat de app zijn sleutel aan een ander apparaat geeft, maar iemand die actief verkeer op je netwerk onderschept of doorstuurt (man-in-the-middle), kan een verbinding nog overnemen. Er is geen kanaalbinding. Gebruik onderweg altijd de tunnel (https).
- Gradle-afhankelijkheden hebben nog geen verificatiebestand met hashes (`gradle/verification-metadata.xml`); dat moet eenmalig in CI of op een ontwikkelmachine gemaakt worden.
- De images worden op versietag gebruikt. Controleer de handtekening met cosign, of pin op digest (staat op de releasepagina).
