# Huis — Android-app

Native Android-app (Kotlin + Jetpack Compose) voor Smart Home Control. De app praat met dezelfde API als de web-app en werkt thuis via het lokale adres en onderweg via je Cloudflare-tunnel.

## Wat zit erin

| Onderdeel | Functie |
|---|---|
| **Start** | Klimaatzones met +/− (één eindwaarde, verzonden 0,8 s na de laatste tik), verlichtingsgroepen aan/uit, huidig verbruik, meldingenbadge. Bovenaan altijd een korte **systeemregel**, ook als alles goed gaat: "Laatste back-up 5 uur geleden · Achtergronddienst actief" (en "Veilige modus aan" als die aan staat), met een stip in groen, oranje of rood. Daaronder een blok **"Let op"** als de achtergronddienst geen hartslag geeft, de veilige modus aan staat, de laatste back-up te oud is of er onderhoud loopt (zelfde regels als het overzicht in de web-app) |
| **Klimaat** | Doeltemperatuur, modus (geblokkeerde modi met uitleg, bv. gedeelde buitenunit), volgende wijziging, het **weekschema** (alleen lezen: dagen met dezelfde blokken samen, het blok dat nu geldt gemarkeerd; aanpassen in de web-app onder Klimaat), 2 uur handmatig, "Terug naar schema" (ook als de airco uit staat) |
| **Energie** | Verbruik per uur en per meter, met kwaliteitslabel (gemeten / afgeleid / geschat / ontbreekt) |
| **Meer** | Meldingen, verbinding, adressen, Cloudflare Access, ontkoppelen |
| **Widget** | Temperatuur van twee zones en het huidige vermogen, met de leeftijd van de gegevens. Ouder dan 30 minuten: gedimd en "Verouderd". De achtergrondtaak ververst de widget ook als de app dicht is. |
| **Meldingen** | Elke 15 minuten haalt de telefoon open waarschuwingen en fouten op en toont nieuwe als melding. Een opgeloste of gezien-gemelde melding verdwijnt weer uit de meldingenbalk. Geen Google-pushdienst nodig. |

Zonder verbinding toont de app de laatst bekende waarden gedimd, met het tijdstip, en zijn alle knoppen uitgeschakeld.

**Commando's.** Een opdracht (temperatuur, modus, lamp) krijgt van de server een nummer. De app toont "Verzonden · wacht op bevestiging…" tot de server meldt wat er gebeurde: *bevestigd*, *opnieuw proberen*, *verzonden maar niet bevestigd*, *mislukt* of *geweigerd* (met de reden). Komt er binnen de tijd geen uitkomst, dan zegt de app dat ook. Staat de airco uit of is hij niet bereikbaar, dan zijn +/−, ventilator en "Handmatig 2 uur" uit; alleen een moduskeuze kan hem starten.

**Rechten.** Bij het koppelen kiest de beheerder een rol voor de telefoon: *bedienen* (standaard), *meekijken* of *beheer*. Een telefoon die alleen mag meekijken, toont alles maar heeft geen knoppen.

## APK installeren

### Release (aanbevolen)

Bij elke release staat op GitHub een ondertekende APK: `huis-v<versie>.apk`, met `.sha256` en de vingerafdruk van het certificaat (`.certificate-sha256`). Alle releases zijn met dezelfde eigen sleutel ondertekend, dus een nieuwe versie installeert gewoon over de oude heen. Instellen van die sleutel: `docs/installatie-truenas.md` › *Android-app: eigen release-sleutel*.

- `versionName` komt uit het bestand `VERSION` in de root van de repository, `versionCode` = major × 10000 + minor × 100 + patch (0.3.12 → 312). Minor en patch moeten onder 100 blijven.
- De release-build wordt door R8 verkleind (`isMinifyEnabled` + `isShrinkResources`); de regels staan in `app/proguard-rules.pro`.
- Gradle ondertekent `assembleRelease` zelf als deze omgevingsvariabelen gezet zijn (zo doet `release.yml` het):

  | Variabele | Inhoud |
  |---|---|
  | `SHC_SIGNING_STORE_FILE` | pad naar de keystore (PKCS12, ook als hij `.jks` heet) |
  | `SHC_SIGNING_STORE_PASSWORD` | wachtwoord van de keystore |
  | `SHC_SIGNING_KEY_ALIAS` | alias, bv. `huis` |
  | `SHC_SIGNING_KEY_PASSWORD` | wachtwoord van de sleutel |
  | `SHC_SIGNING_STORE_TYPE` | optioneel, standaard `pkcs12` |

  Zonder deze variabelen maakt `assembleRelease` een niet-ondertekende APK (`app-release-unsigned.apk`). Er staat nooit een sleutel in de repository.

**Eenmalig opnieuw installeren.** Versie 0.1.0 was met een tijdelijke sleutel ondertekend. Android weigert een update met een andere sleutel: verwijder de oude app, installeer de nieuwe, koppel opnieuw en trek in de web-app (**Account › Telefoons**) de oude koppeling in.

Bij elke release draaien eerst de unit-tests van `core` en `app` (ook als er nog geen release-sleutel is); de APK wordt alleen gebouwd en ondertekend als die slagen.

### Testversie (debug)

Bij elke push die `apps/android` wijzigt bouwt GitHub Actions (workflow **Android**) een debug-APK: **Actions › Android ›** laatste geslaagde run **›** artefact **huis-debug-apk**. Die is met een debugsleutel ondertekend en kan niet over een release heen worden geïnstalleerd (en andersom).

Zelf bouwen kan met Android Studio (Ladybug of nieuwer) of met JDK 17 + Android SDK 35:

```bash
cd apps/android
./gradlew :core:test :app:testDebugUnitTest :app:assembleDebug
```

## Koppelen

1. Stel in de web-app eerst het thuisadres in (**Systeem › Adressen**, bv. `http://192.168.1.20:8088`) en eventueel het adres van je tunnel (`https://…`).
2. Open de web-app als beheerder → **Account › Telefoons**, kies de rol en maak een QR-code. Die is 10 minuten geldig en werkt maar één keer.
3. Open de app, sta de camera toe en scan de code.
4. Lukt scannen niet? Kies **Adres handmatig invoeren**, vul het adres van je server en de koppelcode in. Een adres in je thuisnetwerk mag `http` zijn; elk ander adres moet met `https://` beginnen.

Elke telefoon krijgt een eigen sleutel (token). Die staat versleuteld op de telefoon (Android Keystore) en gaat niet mee in back-ups. De sleutel verloopt na 180 dagen; de app vernieuwt hem zelf zodra er minder dan 30 dagen over zijn (ook op de achtergrond).

### Herkent de app je server?

De QR-code bevat de **server-id** van je installatie. Voordat de app de koppelcode of later zijn sleutel naar een adres stuurt, vraagt hij dat adres zonder sleutel wie het is (`GET /api/v1/pairing/identity`). Antwoordt er een ander apparaat — bijvoorbeeld op een vreemd wifi-netwerk met hetzelfde IP-adres — dan gaat er niets heen en toont de app **"Dit is niet jouw server"**. Hij probeert dan het externe adres.

De server-id is niet geheim: iedereen die de QR-code zag of het adres vraagt, kent hem. Daarom stelt een gekoppelde telefoon bij elke controle ook een **controlevraag**: hij stuurt een nieuw willekeurig getal (`nonce`) en zijn eigen telefoon-id (`client_id`) mee. Alleen jouw server kent de (gehashte) sleutel van deze telefoon en kan het juiste antwoord (`proof`, HMAC-SHA256) geven; de sleutel zelf gaat daarbij niet over de lijn. Op het **thuisadres** (vaak `http`) stuurt de app zijn sleutel alleen na een goed antwoord. Via de **tunnel** (`https`, het certificaat bewijst al wie er antwoordt) is een kloppende server-id genoeg als de server geen antwoord geeft, maar nooit een fout antwoord.

- Een telefoon die gekoppeld is met een oudere app-versie kent zijn telefoon-id nog niet. Hij gebruikt dan eerst de tunnel, haalt daar zijn id op en gebruikt daarna ook weer het thuisadres. Zonder tunnel vraagt de app om opnieuw te koppelen.
- Werk de **server eerst bij** en daarna de app: een oude server geeft geen antwoord op de controlevraag, en dan gebruikt de app het thuisadres niet ("Dit is niet jouw server"); de tunnel blijft werken.

Verder:

- `http` alleen naar adressen in je thuisnetwerk: 10.x, 172.16–31.x, 192.168.x, 169.254.x, IPv6 `fc00::/7` en `fe80::/10`, of namen op `.local`, `.lan`, `.home.arpa`, `.internal`, `.localdomain` of zonder punt. Een QR-code of adres dat zich daar niet aan houdt, weigert de app.
- Het externe adres moet `https` zijn en mag geen thuisadres zijn.
- De Cloudflare Access-gegevens gaan alleen naar het externe adres, nooit naar het thuisadres.
- De app volgt geen doorverwijzingen (redirects).

Android kan "onversleuteld alleen naar privé-adressen" niet in `network_security_config.xml` uitdrukken (dat kent alleen domeinnamen, geen IP-bereiken). Daarom staat onversleuteld verkeer daar aan en dwingt de app de regel zelf af (`core`: `HostRules`, `ServerUrls`, `ApiClient`).

### Thuis of onderweg

De app kiest bij het openen, na elke netwerkwissel (wifi ↔ mobiel) en na een mislukte verbinding opnieuw een route: eerst het thuisadres (binnen 2,5 seconde, "Thuis"), daarna het externe adres ("Onderweg"). Zo schakelt hij thuis vanzelf terug naar het thuisnetwerk. De achtergrondtaak voor meldingen gebruikt dezelfde routekeuze, dus onderweg komen meldingen via de tunnel binnen.

- Een netwerkfout, een serverfout (5xx) of een foutpagina van Cloudflare (52x, 530) telt als "geen verbinding": de app vergeet de route, kiest één keer opnieuw en toont anders de offline-balk. Een melding verschijnt alleen als de status verandert, niet bij elke poging.
- Start de server op of wordt hij bijgewerkt, dan zegt de app dat ("Je huis start op of wordt bijgewerkt").
- Na het verbinden haalt de app de actuele adressen op (`/system/settings`). Heeft de NAS een nieuw IP-adres gekregen of de tunnel een nieuwe naam, dan neemt de app die over (alleen geldige adressen; ook een nieuw adres moet zich met de juiste server-id melden). Onder **Meer › Adressen bewerken** kun je ze ook zelf aanpassen.

### Ontkoppelen en "niet meer gekoppeld"

- **Meer › Ontkoppelen** trekt de sleutel eerst op de server in en wist daarna alles op de telefoon. Is de server niet bereikbaar, dan wist de app alleen zijn eigen gegevens; trek de sleutel dan in via de web-app (**Account › Telefoons**).
- Trekt een beheerder de sleutel in, is hij verlopen, of kent de server de telefoon niet meer (bijvoorbeeld na een herstel), dan toont de app **"Deze telefoon is niet meer gekoppeld"** in plaats van "Geen verbinding", en stuurt hij de oude sleutel nergens meer heen. Koppel opnieuw met een QR-code.
- Is de versleutelde opslag op de telefoon beschadigd (zeldzaam, bv. na een systeemupdate), dan crasht de app niet: hij wist de opslag en vraagt om opnieuw te koppelen.

## Meldingen op de telefoon

Android 13 en nieuwer vraagt toestemming voor meldingen. Meldingen die binnenkomen terwijl er geen toestemming is, gaan niet verloren: de app houdt ze vast en toont ze zodra meldingen zijn toegestaan (zolang ze nog open staan). Wordt een melding opgelost of als gezien gemarkeerd, dan haalt de app hem uit de meldingenbalk.

## Cloudflare Tunnel en Access

De API heeft zelf al inloggen en sleutels, maar zet bij voorkeur ook **Cloudflare Access** voor je tunnel, zodat onbekenden de server niet eens bereiken.

1. Cloudflare Zero Trust → **Access › Applications** → voeg een *Self-hosted* applicatie toe voor je hostnaam (bv. `huis.voorbeeld.nl`).
2. Maak onder **Access › Service Auth** een **Service Token**. Noteer de *Client ID* en *Client Secret* (het secret zie je maar één keer).
3. Voeg aan de applicatie een policy toe met actie **Service Auth** en de service token als regel. Houd daarnaast je gewone policy (bv. e-mail-login) voor de browser.
4. Vul in de app onder **Meer › Cloudflare Access** de Client ID en het secret in. De app stuurt ze als `CF-Access-Client-Id` en `CF-Access-Client-Secret` mee naar het externe adres, ook voor de live-verbinding (WebSocket). Ontbreken ze of kloppen ze niet, dan meldt de app dat Cloudflare Access hem niet doorlaat.

Tips:
- Zorg dat WebSockets aanstaan voor je zone (Cloudflare dashboard → Network → WebSockets).
- Stel geen "Browser Integrity Check" of bot-blokkade in die API-verzoeken van de app tegenhoudt.
- Het secret wordt net als de sleutel versleuteld opgeslagen. Wissen = beide velden leegmaken en opslaan.

## Projectopbouw

```
apps/android
├── core/   Pure Kotlin (JVM): API-modellen, HTTP- en WebSocket-client, QR-payload,
│           adresregels en serverherkenning, routekeuze met één herkansing,
│           foutindeling (offline/serverfout/niet meer gekoppeld), commando-volging,
│           debounce, tokenvernieuwing, meldingenplan, relatieve tijd en
│           schemadagen als getallen, Nederlandse getalnotatie.
│           Heeft geen Android nodig en is los te testen.
└── app/    Android: Compose-UI, versleutelde opslag, repository, netwerkwissels,
            widget (Glance), WorkManager, CameraX + ML Kit QR-scanner.
```

Belangrijke klassen voor wie de schermen bouwt:

- `data/HomeRepository` — één bron van waarheid voor UI, widget en achtergrondtaak. `state` (`HomeState`) bevat o.a. `connection` (verbonden, offline, opstarten, *niet jouw server*, *niet meer gekoppeld*), `unpaired` (reden), `readOnly`, `commands` (status per zone of lampgroep) en `drafts`.
- `core/Network.kt` — adresregels, serverherkenning, routekeuze, foutindeling.
- `core/Commands.kt` — commando-volging en debounce.
- `core/Schedule.kt` — weekschema: dagen groeperen (`dayParts`), het blok dat nu geldt.

Teksten die de gebruiker ziet staan in `app/src/main/res/values/strings.xml`. `core` kent geen Android-resources en geeft daarom codes terug, die `app/src/main/java/nl/huis/smarthome/data/CoreTexts.kt` omzet naar een tekst uit `strings.xml`:

| Code in `core` | Waarvoor |
|---|---|
| `AddressProblem` | Adresregels (thuisadres, extern adres) |
| `ConnectionChange` | Eenmalige verbindingsmeldingen (verbinding weg, server start op, niet jouw server, weer verbonden) |
| `PairError` | Geweigerde QR-code of handmatige invoer |
| `UnpairReason` | Waarom de telefoon niet meer gekoppeld is |
| `ApiProblem` | Fouten die de app zelf beschrijft: Cloudflare Access, tunnel onbereikbaar, doorverwijzing, wifi-inlogpagina, onbekend adres, onversleuteld geweigerd, kale HTTP-statuscodes |
| `CommandLabel` | De statusregel onder een zone of lampgroep (*Verzonden · wacht op bevestiging…*, *Bevestigd*, *Mislukt*, *Geweigerd* …) |
| `RelativeTime` | Hoe lang geleden, als getallen: *zojuist*, *3 min geleden*, *2 uur geleden*, *9 dagen geleden*, *vandaag 08:05*, *gisteren 14:05*, *3 nov 09:12* (`CoreTexts.relative`, met `<plurals>`) |
| `DataAge` | De leeftijd van de gegevens in de widget: *Bijgewerkt 3 min geleden*, *Nog niet bijgewerkt* (`CoreTexts.age`) |
| `DayParts` | De dagen van het weekschema: *elke dag*, *ma–vr*, *za en zo*, *ma, wo en vr*; voor TalkBack *maandag t/m vrijdag* (`CoreTexts.days`) |

De `when`-blokken in `CoreTexts` zijn volledig: een nieuwe code in `core` compileert pas als er een tekst bij staat, en de test `CoreTextsTest` controleert dat elke code een eigen tekst heeft die overeenkomt met strings.xml. Elke code houdt een Nederlandse terugvaltekst (`dutch`) voor de JVM-tests. Meldingen van de server zelf (de reden na *Mislukt:* of *Geweigerd:*) toont de app zoals de server ze stuurt.

Tijden en dagen geeft `core` als getallen terug (`RelativeTime.of`, `RelativeTime.ofAge`, `Freshness.age`, `WeekSchedule.dayParts`); de app maakt er tekst van met `strings.xml` en `<plurals>`. De formatters in `CoreTexts` lezen hun teksten via `TextSource`: in de app de Android-resources, in de unit-tests `strings.xml` zelf (`FormattedTextsTest` controleert zo de Nederlandse uitkomst). Alleen de getalnotatie (`NlFormat`: "20,6°", "1.240 W", "€ 2,18") staat nog in `core`.

De systeemregel op Start komt uit `Overview.status`: `age_h` van de regel `backup` (leeftijd van de laatste goede back-up), de toestand en `age_s` van `worker` (hartslag; zonder bruikbare toestand telt een hartslag jonger dan 120 s als actief), `maintenance` (achtergronddienst gepauzeerd) en `safe_mode`. Wat de server niet meldt, laat de regel weg (`SystemLineUi` in `ui/Presentation.kt`).

De `core`-tests draaien op elke JVM (`./gradlew :core:test`). Kan de Android SDK niet worden geïnstalleerd, dan kun je `core` testen met een los Gradle-project dat `core/` als project insluit en alleen Maven Central gebruikt.

**Bouwen en afhankelijkheden.** De Gradle-wrapper controleert de download met een vaste SHA-256 (`distributionSha256Sum` in `gradle/wrapper/gradle-wrapper.properties`, van https://gradle.org/release-checksums/). Pas je `distributionUrl` aan, werk dan ook de checksum bij; de wrapper-updates van Dependabot doen dat zelf. Een verificatiebestand voor alle Gradle-afhankelijkheden (`gradle/verification-metadata.xml`) is er nog niet: dat moet gemaakt worden waar Google Maven bereikbaar is (CI of een ontwikkelmachine) met `./gradlew --write-verification-metadata sha256 help :app:assembleRelease :core:test`, en daarna in git.

Ontwerp: de Android-artboards in het ontwerp-canvas (Start, Klimaat, Energie, Koppelen). Kleuren staan in `app/src/main/java/nl/huis/smarthome/ui/theme/Theme.kt`, met een donkere variant. De app gebruikt het systeemlettertype in plaats van Space Grotesk/Inter, zodat er geen lettertypebestanden of Google Play-diensten nodig zijn.
