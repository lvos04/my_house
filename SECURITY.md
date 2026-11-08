# Beveiliging

## Uitgangspunten

- **LAN-first.** Geen poorten open in de router, geen analytics, geen verplichte cloud. Toegang van buiten alleen via de optionele Cloudflare Tunnel, liefst met Cloudflare Access ervoor.
- **Geen standaardwachtwoord.** Het eerste account maak je zelf aan (minimaal 12 tekens), daarna is `/auth/setup` dicht.
- **Wachtwoorden:** argon2id. Inlogpogingen beperkt (5 per 5 minuten per IP en gebruiker).
- **Web-sessies:** cookie `HttpOnly`, `SameSite=Strict`, `Secure` achter HTTPS, plus CSRF-token op elke wijziging.
- **Telefoons:** eigen sleutel per toestel via een eenmalige QR-code (10 minuten geldig). Opgeslagen als hash op de server, versleuteld op de telefoon, intrekbaar.
- **Rollen:** `admin` en `viewer` (alleen lezen). Elke wijziging komt in het auditlog.
- **Geheimen:** apparaatsleutels versleuteld in de database (AES-256-GCM, sleutel uit `SHC_SECRET_KEY_FILE`). Database- en brokerwachtwoorden als Docker-secrets. Nooit in logs of in de frontend.
- **Apparaten met eigen certificaat** (Hue, HomeWizard v2): het certificaat wordt bij het koppelen vastgelegd; een ander certificaat wordt geweigerd.
- **Containers:** vaste image-versies, niet-root, alleen-lezen bestandssysteem waar mogelijk, `no-new-privileges`, geheugenlimieten. De MQTT-broker is niet van buiten de stack bereikbaar en heeft per gebruiker een ACL.
- **Boiler:** het schakelen van de boiler-stekker is geblokkeerd in de adapter, in de API en in de worker vlak voor verzenden. Dat geldt voor alle routes, rollen, knoppen en de klimaatregelaar. De HomeWizard-app zelf kan de stekker nog wel schakelen; zet daar eventueel de schakelaarvergrendeling aan.
- **Na herstel of verhuizing:** veilige modus, geen apparaatcommando's tot jij bevestigt.

## Kwetsbaarheid melden

Open een privé *security advisory* in deze GitHub-repository. Niet als gewoon issue.

## Bekende beperkingen

- Lokale apparaat-API's (Gree, HomeWizard v1) gebruiken geen of zwakke versleuteling. Iedereen in je wifi kan ze in principe bedienen. Zet IoT-apparaten bij voorkeur in een apart VLAN of gastnetwerk dat de NAS wel bereikt.
- De images worden op versietag gebruikt. Pin voor extra zekerheid op digest (`docker buildx imagetools inspect`).
- Met `render_truenas.py --with tunnel` staat het tunneltoken in de YAML van de app.
