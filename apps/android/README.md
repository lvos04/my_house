# Huis — Android-app

Native Android-app (Kotlin + Jetpack Compose) voor Smart Home Control. De app praat met dezelfde API als de web-app (`docs/api.md`) en werkt thuis via het lokale adres en onderweg via je Cloudflare-tunnel.

## Wat zit erin

| Scherm | Functie |
|---|---|
| **Start** | Klimaatzones met +/− (verzonden na 0,8 s stilstand), verlichtingsgroepen aan/uit, huidig verbruik, meldingenbadge |
| **Klimaat** | Draaiknop met doeltemperatuur, modus (geblokkeerde modi met uitleg, bv. gedeelde buitenunit), schema-info, 2 uur handmatig / terug naar schema |
| **Energie** | Verbruik vandaag per uur, per meter met kwaliteitslabel (gemeten / afgeleid / geschat / ontbreekt) |
| **Meer** | Meldingen, verbindingsinfo, Cloudflare Access, ontkoppelen |
| **Widget** | Temperatuur van twee zones en het huidige vermogen (uit de laatst bekende gegevens) |
| **Meldingen** | Elke 15 minuten haalt de telefoon open waarschuwingen/fouten op en toont nieuwe als melding. Geen Google-pushdienst nodig. |

Zonder verbinding toont de app de laatst bekende waarden gedimd, met het tijdstip, en zijn alle knoppen uitgeschakeld. Een commando toont "Verzonden…" tot de airco het via readback bevestigt.

## APK installeren

De APK wordt gebouwd door GitHub Actions (workflow **Android**, `.github/workflows/android.yml`) bij elke push die `apps/android` wijzigt.

1. Open de repository op GitHub → **Actions** → **Android** → de laatste geslaagde run.
2. Download onderaan het artefact **huis-debug-apk** (een zip) en pak het uit.
3. Zet `app-debug.apk` op je telefoon en open het. Sta "installeren uit onbekende bron" toe voor je bestandsbeheerder of browser als Android daarom vraagt.

Dit is een debug-build (gesigneerd met een debugsleutel). Voor een eigen release-build: maak een keystore (`keytool -genkeypair -v -keystore huis.jks -keyalg RSA -keysize 4096 -validity 10000 -alias huis`), voeg een `signingConfig` toe aan `app/build.gradle.kts` en bewaar de wachtwoorden als GitHub-secrets — nooit in de repository.

Zelf bouwen kan met Android Studio (Ladybug of nieuwer) of met JDK 17 + Android SDK 35:

```bash
cd apps/android
./gradlew :core:test :app:testDebugUnitTest :app:assembleDebug
```

## Koppelen

1. Open de web-app als beheerder → **Systeem › Telefoon koppelen**. Er verschijnt een QR-code die 10 minuten geldig is en maar één keer werkt.
2. Open de app, sta de camera toe en scan de code. De app probeert eerst het thuisadres en daarna het externe adres.
3. Lukt scannen niet? Kies **Adres handmatig invoeren**, vul het adres van je server in (bv. `192.168.1.20:8080` of `https://huis.voorbeeld.nl`) en de koppelcode die onder de QR-code staat.

Elke telefoon krijgt een eigen sleutel. Die staat versleuteld op de telefoon (Android Keystore) en gaat niet mee in back-ups. Telefoon kwijt? Trek de sleutel in via de web-app (**Systeem › Telefoon koppelen**).

### Thuis of onderweg

Bij het openen kijkt de app eerst of het thuisadres binnen 2,5 seconde antwoordt ("Thuis"). Zo niet, dan gaat hij via het externe adres ("Onderweg"). Het externe adres moet `https` zijn.

## Cloudflare Tunnel en Access

De API heeft zelf al inloggen en sleutels, maar zet bij voorkeur ook **Cloudflare Access** voor je tunnel, zodat onbekenden de server niet eens bereiken.

1. Cloudflare Zero Trust → **Access › Applications** → voeg een *Self-hosted* applicatie toe voor je hostnaam (bv. `huis.voorbeeld.nl`).
2. Maak onder **Access › Service Auth** een **Service Token**. Noteer de *Client ID* en *Client Secret* (het secret zie je maar één keer).
3. Voeg aan de applicatie een policy toe met actie **Service Auth** en de service token als regel. Houd daarnaast je gewone policy (bv. e-mail-login) voor de browser.
4. Vul in de app onder **Meer › Cloudflare Access** de Client ID en het secret in. De app stuurt ze mee als `CF-Access-Client-Id` en `CF-Access-Client-Secret`, ook voor de live-verbinding (WebSocket).

Tips:
- Zorg dat WebSockets aanstaan voor je zone (Cloudflare dashboard → Network → WebSockets).
- Stel geen "Browser Integrity Check" of bot-blokkade in die API-verzoeken van de app tegenhoudt.
- Het secret wordt net als de sleutel versleuteld opgeslagen. Wissen = beide velden leegmaken en opslaan.

## Projectopbouw

```
apps/android
├── core/   Pure Kotlin (JVM): API-modellen, HTTP- en WebSocket-client,
│           QR-payload, routekeuze, Nederlandse opmaak, backoff, meldingenfilter.
│           Heeft geen Android nodig en is los te testen.
└── app/    Android: Compose-UI, versleutelde opslag, repository,
            widget (Glance), WorkManager, CameraX + ML Kit QR-scanner.
```

Ontwerp: de Android-artboards in het ontwerp-canvas (Start, Klimaat, Energie, Koppelen). Kleuren staan in `app/src/main/java/nl/huis/smarthome/ui/theme/Theme.kt`, met een donkere variant. De app gebruikt het systeemlettertype in plaats van Space Grotesk/Inter, zodat er geen lettertypebestanden of Google Play-diensten nodig zijn.
