# Verbeterlijst smart-home-app

De code is bekeken vanuit acht invalshoeken: klimaatregeling, beveiliging, energiedata, koppelingen, web, Android, beheer en het implementatieplan. Dubbele bevindingen zijn samengevoegd.

Deze lijst heeft **45 genummerde punten** plus een lijst kleine punten. De drie grootste thema's zijn:
1. **De eerste installatie loopt op meerdere plekken vast.** Je kunt geen woning aanmaken. Thuis inloggen en live-updates werken niet meer zodra de tunnel aan staat. Nieuwe Zigbee-sensoren en nieuwere Gree-modules worden niet herkend.
2. **De klimaatregeling kan onverwacht uit blijven of juist aangaan.**
3. **Alle ruwe meetdata blijft voor altijd bewaard en wordt in één keer in het geheugen geladen.** Daardoor laat de nachtelijke back-up de worker binnen enkele dagen crashen, en loopt de energiepagina vast.

Daarnaast zitten er beveiligingsgaten in die echt gaan tellen zodra de Cloudflare-tunnel aan staat (punten 16–18).

**Leeswijzer**
- Inspanning: **S** = klein (uren tot een dag), **M** = middel (enkele dagen), **L** = groot (een week of meer).
- De *worker* is het achtergrondproces dat de apparaten uitleest, de klimaatregeling doet en back-ups maakt. De *API* is de server achter de web-app en de Android-app.
- "Niet helemaal zeker" betekent: de fout staat echt in de code, maar een deel van het gevolg is niet bevestigd, of minder erg dan het eerst leek.
- De codes tussen haakjes (bijvoorbeeld WEB-1) verwijzen naar de oorspronkelijke bevindingen.

---

## Eerst doen (vóór/tijdens eerste installatie)

### Installatie en eerste start

**1. Een verse installatie loopt vast: zonder woning is geen enkele pagina bereikbaar** _(WEB-1, PLAN-10)_
Zolang er geen woning is, toont elke pagina (ook Systeem) alleen de kaart "Maak eerst een woning aan via Systeem › Installatie". Die sectie bestaat niet. Het enige formulier om een woning te maken zit juist áchter die kaart. Na het aanmaken van het beheerdersaccount kom je dus niet verder zonder handmatige API-aanroepen (curl).
- **Oplossing:** Zet in die kaart direct een formulier "Woning aanmaken" (naam + tijdzone), of laat de pagina Systeem altijd zien. Pas de tekst aan en voeg een test toe voor "nog geen woning". Pas ook de installatiehandleiding aan: die stuurt je na het account meteen naar Apparaten.
- **Inspanning:** S
- **Bestanden:** `apps/web/src/components/Layout.tsx`, `apps/web/src/pages/System.tsx`, `apps/api/src/shc_api/routers/auth.py`, `apps/api/src/shc_api/routers/homes.py`, `docs/installatie-truenas.md`

**2. Installatie strandt op de privé-repository en de privé-images** _(OPS-4)_
De repository is privé. Een `git clone` vanuit de TrueNAS-shell vraagt dus om inloggegevens, en GitHub accepteert daar alleen een token. De images op ghcr.io zijn daardoor ook privé, en het starten van de app faalt met "unauthorized". De handleiding zegt hier maar één vage zin over.
- **Oplossing:** Kies een van deze drie:
  - Maak de packages openbaar.
  - Hang een installatiebundel (install.sh, render_truenas.py, config/, compose.yaml) als bijlage aan de release.
  - Beschrijf stap voor stap hoe je een token met `read:packages` maakt en waar je dat in TrueNAS invult (Apps › Configuration › registries, ghcr.io).

  Laat `install.sh` daarnaast vooraf een `docker pull` proberen, met een duidelijke foutmelding als dat mislukt.
- **Inspanning:** S
- **Bestanden:** `docs/installatie-truenas.md`, `infra/scripts/install.sh`, `.github/workflows/release.yml`

**3. Zodra de tunnel aan staat, werkt thuis inloggen via http://NAS:8088 niet meer** _(SECU-6, OPS-3)_
Met een https-adres voor buitenshuis zet `render_truenas.py` de inlogcookie vast op "alleen via https". Browsers slaan zo'n cookie niet op via gewone http. Inloggen lijkt dan te lukken, maar elke volgende stap geeft "niet ingelogd". De handleiding raadt precies deze combinatie aan. Wie het omzeilt door de instelling uit te zetten, stuurt de sessiecookie buitenshuis soms onversleuteld mee. Er is ook geen HSTS (de browser dwingen altijd https te gebruiken).
- **Oplossing:** Bepaal per verzoek of het via https binnenkwam. Laat nginx de `X-Forwarded-Proto` van cloudflared doorgeven (alleen vertrouwd vanaf het interne netwerk) en zet de cookie alleen dan op "Secure". Zet in Cloudflare "Always Use HTTPS" en HSTS aan en schrijf dat in `docs/buiten-huis.md`. Voeg een test toe: inloggen via http terwijl een remote-URL is ingesteld.
- **Inspanning:** S
- **Bestanden:** `infra/scripts/render_truenas.py`, `apps/api/src/shc_api/routers/auth.py`, `apps/web/nginx.conf.template`, `docs/buiten-huis.md`

**4. Thuis komen nooit live-updates binnen, en de web-app springt elke ~11 seconden naar "Geen verbinding"** _(WEB-2)_
nginx laat het poortnummer (8088) weg bij het doorsturen. De API ziet daardoor een verschil tussen adres en herkomst en weigert de inlogcookie op de live-verbinding (WebSocket). Na 10 seconden verbreekt de API die verbinding. De web-app ziet dat als netwerkverlies: hij toont de offline-balk, schakelt de knoppen tijdelijk uit en laadt alles opnieuw, elke ~11 seconden. Via de tunnel (poort 443) werkt het wel, en daarom is de fout lastig te vinden. Bedienen werkt nog wel via gewone verzoeken, maar zonder live-updates en zonder bevestigingen.
- **Oplossing:** Gebruik in nginx `proxy_set_header Host $http_host;`, zodat de poort behouden blijft. Behandel in `ws.ts` sluitcode 4401 apart: opnieuw inloggen in plaats van "offline". Meld pas "offline" na een paar mislukte pogingen. Test achter nginx op een andere poort dan 80/443.
- **Inspanning:** S
- **Bestanden:** `apps/web/nginx.conf.template`, `apps/api/src/shc_api/routers/ws.py`, `apps/web/src/ws.ts`

**5. De nachtelijke back-up laat de worker binnen enkele dagen crashen** _(OPS-1, ENER-3, PLAN-4)_
De back-up laadt elke tabel volledig in het geheugen, inclusief alle meterstanden. Dat zijn ±100.000 rijen per dag, en ze worden nooit opgeruimd. Gemeten: ±3 dagen aan metingen kost al ±580 MB, terwijl de worker maar 384 MB mag gebruiken.

Zodra je de back-up-wachtwoordzin instelt (wat de handleiding aanraadt), wordt de worker na een paar dagen elke nacht afgeschoten. Is er nog nooit een back-up gelukt, dan gebeurt dat zelfs bij elke herstart (een crash-lus). Daarmee liggen ook de klimaatregeling en het uitlezen van apparaten stil, en komen er geen back-ups meer. Een handmatige back-up via de web-app loopt in de API tegen dezelfde grens aan (512 MB).
- **Oplossing:**
  - Lees de tabellen in stukjes (server-side cursor) en schrijf direct naar een bestand op schijf. Versleutel en controleer ook in stukjes.
  - Laat de back-up in een apart proces of een aparte container draaien, zodat de worker niet blokkeert. Overweeg dagelijks `pg_dump` plus wekelijks de draagbare export (plan §10).
  - Registreer een mislukte poging, zodat de back-up niet meteen opnieuw start.

  Het opruimen van oude ruwe metingen staat in punt 19.
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/backup.py`, `apps/api/src/shc_api/worker.py`, `apps/api/src/shc_api/routers/system.py`, `infra/compose/compose.yaml`

**6. De Android-app wordt bij elke release met een andere sleutel ondertekend, dus updaten lukt niet** _(OPS-5, ANDR-3, SECU-9)_
De release bouwt een debug-versie, ondertekend met een sleutel die bij elke build nieuw is, en `versionCode` staat vast op 1. Een nieuwe versie over de oude installeren mislukt daardoor. Je moet de app verwijderen (koppeling en Cloudflare-gegevens zijn dan weg) en opnieuw koppelen via QR, terwijl het oude token op de server geldig blijft. Een debug-versie is bovendien makkelijker via USB uit te lezen.
- **Oplossing:** Maak één vaste release-sleutel. Bewaar die als GitHub-secret, met een kopie buiten GitHub. Bouw `assembleRelease` en controleer de handtekening met `apksigner verify`. Haal `versionCode`/`versionName` uit het bestand VERSION. Doe dit nu, zolang er één gebruiker is: dan hoef je maar één keer opnieuw te installeren.
- **Inspanning:** M
- **Bestanden:** `.github/workflows/release.yml`, `apps/android/app/build.gradle.kts`

**7. Een tarief opslaan lukt niet als je "Vaste kosten (optioneel)" leeg laat** _(ENER-11)_
De web-app stuurt dan een lege waarde mee, maar de API eist een getal en geeft een algemene foutmelding. Zonder tarief zie je nergens kosten. Tijdelijke omweg: vul 0 in.
- **Oplossing:** Maak het veld in de API echt optioneel (leeg = 0), of laat de web-app het veld weglaten. Voeg een test toe met een leeg veld.
- **Inspanning:** S
- **Bestanden:** `apps/web/src/pages/Energy.tsx`, `apps/api/src/shc_api/routers/energy.py`

### Echte apparaten koppelen

**8. Nieuw gekoppelde Zigbee-sensoren verschijnen nergens in de app** _(ADAP-2, PLAN-3)_
Tijdens het koppelen stuurt Zigbee2MQTT eerst meldingen ("aangemeld", "interview", "beschikbaar") en pas daarna metingen. Bij de eerste melding maakt de worker een apparaat aan van het algemene type "device", en dat type past hij later nooit meer aan. De app toont alleen apparaten van het type "zigbee_sensor". Je twee sensoren verschijnen dus niet als gekoppeld, en je kunt ze niet kiezen als sensor voor een klimaatzone.

Verder:
- De status van de Zigbee-coördinator komt onder een verkeerde naam binnen, waardoor er een extra apparaat "bridge" verschijnt.
- Sensoren kun je niet kalibreren (geen offset).
- **Oplossing:** Bepaal het type bij het aanmaken uit de apparatenlijst van Zigbee2MQTT, en werk "device" bij naar het juiste type zodra dat bekend is. Gebruik de friendly_name als naam. Koppel de "bridge"-status aan het bestaande coördinator-apparaat. Voeg per sensor een offset voor temperatuur en vochtigheid toe (en bewaar de ruwe waarde). Voeg een worker-test toe die de volgorde aanmelden → interview → beschikbaarheid → meting naspeelt.
- **Inspanning:** S (met kalibratie: M)
- **Bestanden:** `apps/api/src/shc_api/worker.py`, `integrations/src/shc_integrations/zigbee/adapter.py`, `apps/web/src/pages/Devices.tsx`, `apps/web/src/pages/Climate.tsx`

**9. Nieuwere Gree-wifi-modules koppelen niet ("reageert niet")** _(ADAP-3)_
De adapter raadt het type versleuteling uit het zoekantwoord van de unit. Nieuwere modules antwoorden in het oude formaat maar willen de nieuwe versleuteling, en daardoor krijgt de koppelpoging geen antwoord. De referentiebibliotheek (die Home Assistant gebruikt) probeert daarom eerst de oude en dan de nieuwe. Gevolg: je denkt dat je airco niet ondersteund wordt, terwijl hij wel zou werken.
- **Oplossing:** Probeer bij het koppelen eerst de oude versleuteling, en bij geen antwoord de nieuwe. Sla op welke werkt en noteer dat in de diagnostiek. Voeg een test toe voor dit geval.
- **Inspanning:** S
- **Bestanden:** `integrations/src/shc_integrations/gree/adapter.py`

### Veiligheid: klimaat en boiler

**10. Een wijziging met de afstandsbediening blijft eindeloos van kracht** _(CONT-3)_
Als iemand de Gree-afstandsbediening gebruikt, pauzeert de app het schema 2 uur. Na die 2 uur vergelijkt de app opnieuw met zijn eigen oude waarden. Hij "ziet" de wijziging dan opnieuw en start weer 2 uur, en zo eindeloos door. Het schema werkt dan niet meer tot iemand op "Terug naar schema" drukt. Het logboek vult zich elke 2 uur met tegenstrijdige regels. Een afstandsbediening wordt vaak gebruikt, dus dit gebeurt snel.
- **Oplossing:** Sla bij het detecteren van een wijziging de waargenomen stand op als nieuw uitgangspunt (`last_commanded`). Ga na afloop terug naar het schema zonder opnieuw met de oude waarden te vergelijken. Voeg een test toe die over het verlopen van de 2 uur heen loopt.
- **Inspanning:** S
- **Bestanden:** `apps/api/src/shc_api/climate.py`, `apps/api/src/shc_api/worker.py`

**11. Eén mislukt commando kan de verwarming blijvend uitzetten** _(CONT-2, CONT-7)_
De app onthoudt "dit heb ik gestuurd" al op het moment dat het commando in de wachtrij komt, niet pas als het bevestigd is. Stel dat het mislukt: één verloren netwerkpakket, of de unit is even niet bereikbaar. Dan ziet de volgende ronde "unit uit, maar ik stuurde verwarmen", en concludeert de app dat iemand de unit met de afstandsbediening heeft uitgezet. De zone blijft dan uit, zonder melding, en de app geeft de afstandsbediening de schuld.

Daar komt bij: bij een uitgeschakelde zone is de knop "Terug naar schema" niet zichtbaar (web en Android). Je moet de unit eerst in een modus aanzetten.
- **Oplossing:**
  - Werk `last_commanded` pas bij na een bevestigde terugmelding, en sla het openstaande commando apart op.
  - Sla de detectie van "externe wijziging" over zolang er een commando onderweg is, en nog één uitleesronde daarna. Eis dat het verschil in minstens twee uitleesrondes te zien is.
  - Bij een mislukt commando: geef een melding, probeer het een beperkt aantal keren opnieuw, en verander de regelstand niet.
  - Toon "Terug naar schema" ook bij de stand "uit" (bijvoorbeeld via een `can_resume`-vlag).
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/worker.py`, `apps/api/src/shc_api/climate.py`, `apps/api/src/shc_api/views.py`, `apps/web/src/pages/Climate.tsx`, `apps/android/app/src/main/java/nl/huis/smarthome/ui/screens/ClimateScreen.kt`

**12. Je kunt geen schema's instellen, en een schemablok zonder modus zet de airco uit** _(WEB-4, PLAN-2, CONT-4)_
Er is geen schema-editor in web of Android. Nieuwe zones hebben geen schema, dus de regeling verwarmt of koelt nooit uit zichzelf. Een schema instellen kan alleen via de API.

Daar zit een tweede fout. `docs/api.md` beschrijft blokken zonder modus, en de code zegt "geen modus = huidige modus houden". Maar de regeling valt dan terug op de standaardwaarde "uit". Zo'n blok zet een draaiende unit dus uit, of start hem nooit.
- **Oplossing:**
  - Maak een weekschema-editor: blokken per dag (begintijd, doeltemperatuur, modus, label), "dag kopiëren", sjablonen (constant, nachtverlaging, voorverwarmen), controle op invoer en een voorbeeld van de "volgende wijziging". Sla op via de bestaande `PUT /zones/{id}/schedule`.
  - Laat "geen modus" echt de huidige of laatst gekozen modus houden, en laat zo'n blok nooit een uitgeschakelde unit starten. Of maak modus verplicht.
  - Voeg `mode` toe aan `docs/api.md` en aan het web-type, en test blokken zonder modus.
- **Inspanning:** M
- **Bestanden:** `apps/web/src/pages/Climate.tsx`, `apps/web/src/api/client.ts`, `apps/web/src/api/types.ts`, `apps/api/src/shc_api/climate.py`, `apps/api/src/shc_api/routers/zones.py`, `docs/api.md`

**13. Alleen de temperatuur of de ventilator wijzigen zet een uitgeschakelde unit aan, in de oude modus** _(CONT-6)_
Elk commando dat niet "uit" is, krijgt automatisch "aan" erbij. Druk je op "Handmatig 2 uur" of verander je de ventilator terwijl de unit uit staat, dan start hij in de laatst opgeslagen modus, bijvoorbeeld koelen van afgelopen zomer. Er wordt dan ook niet gecontroleerd of dat botst met de andere kamer. Op web en Android is die knop gewoon actief bij een uitgeschakelde unit.
- **Oplossing:** Voeg "aan" alleen toe als er expliciet een modus is gekozen. Als alleen temperatuur of ventilator verandert bij een uitgeschakelde unit: verstuur het zonder "aan", of weiger met "Kies eerst een modus". Schakel "Handmatig 2 uur" uit zolang de unit uit staat.
- **Inspanning:** S
- **Bestanden:** `apps/api/src/shc_api/worker.py`, `apps/api/src/shc_api/climate.py`, `apps/web/src/pages/Climate.tsx`, `apps/android/app/src/main/java/nl/huis/smarthome/ui/screens/ClimateScreen.kt`

**14. Klimaatcommando's worden vlak vóór uitvoering niet opnieuw gecontroleerd** _(CONT-1, CRIT-4)_
Dit heeft twee gevolgen:
- **Verwarmen en koelen tegelijk.** De controle "niet verwarmen en koelen tegelijk op dezelfde buitenunit" kijkt alleen naar wat de andere unit op dit moment meldt. Commando's die nog in de wachtrij staan, een unit die even offline is en de modus "auto" worden genegeerd. Knopacties slaan de controle helemaal over.
- **Een "uit" wordt ongedaan gemaakt.** Druk je op "uit" terwijl de regeling net een ronde draait, dan kan het oudere "verwarmen"-commando van de regeling ná jouw "uit" worden uitgevoerd. De unit blijft dan aan, terwijl de app "Uitgezet" toont.

Niet helemaal zeker hoe erg dit is. Een Gree-buitenunit lost een modusconflict zelf op: één kamer krijgt dan niets, maar er gaat niets kapot. Beide situaties zijn zeldzaam.
- **Oplossing:**
  - Bepaal per unit een "effectieve modus": het nieuwste openstaande commando, en anders de laatste terugmelding. Houd van een offline unit de laatst bekende modus nog even vast. Laat "auto" botsen met zowel verwarmen als koelen.
  - Beslis de zones van één buitenunit samen.
  - Controleer opnieuw vlak vóór uitvoering, onder een vergrendeling (rij-lock per zone of advisory lock per buitenunit), in zowel de API als de worker.
  - Gooi regelcommando's weg voor een zone die inmiddels op handmatig of uit staat.
  - Stuur knop- en API-acties door dezelfde controle.
  - Voeg een databasetest met twee zones toe.
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/views.py`, `apps/api/src/shc_api/worker.py`, `apps/api/src/shc_api/climate.py`, `apps/api/src/shc_api/routers/zones.py`

**15. Niemand merkt het als de stekker van de boiler uitgaat** _(CONT-9, ADAP-10)_
De app kan de boilerstekker terecht niet schakelen. Maar hij controleert ook niet of de stekker aan staat, terwijl die stand wel wordt uitgelezen. Wordt de stekker uitgezet via de HomeWizard-app, de knop op de stekker of een automatisering, dan krijgt de boiler (2000 W) geen stroom, en ziet de app 0 W als normaal verbruik. In de praktijk merk je het snel aan het ontbreken van warm water, maar een melding is goedkoop.
- **Oplossing:** Geef een kritieke melding als een beschermde stekker `relay_on = false` meldt. Geef een waarschuwing als `switch_lock` uit staat, met uitleg hoe je "Switch lock" aanzet in de HomeWizard Energy-app (controleer de precieze werking in de HomeWizard-documentatie). Toon de stand op de apparaatpagina. De app zelf blijft de stekker niet kunnen schakelen.
- **Inspanning:** S
- **Bestanden:** `apps/api/src/shc_api/worker.py`, `integrations/src/shc_integrations/homewizard/adapter.py`

### Beveiliging via de Cloudflare-tunnel

**16. De inlogbeveiliging is te omzeilen, en de API is met weinig moeite plat te leggen** _(SECU-1, SECU-4, SECU-3)_
Drie samenhangende problemen:
- **Limiet op inlogpogingen werkt niet.** De API vertrouwt de `X-Forwarded-For`-header van iedereen, en nginx vult die aan in plaats van hem te overschrijven. Een aanvaller kiest zo per poging een nieuw "IP-adres". Daardoor werkt de limiet van 5 pogingen niet, toont het auditlog verzonnen adressen, en groeit de lijst met pogingen eindeloos.
- **Inlogpogingen bevriezen de API.** Het controleren van een wachtwoord (Argon2, ±55 ms) gebeurt op de enige hoofdlus van de API. ±18 inlogpogingen per seconde bevriezen de hele API: app, web, live-updates en healthcheck.
- **Eén groot verzoek laat de API crashen.** nginx accepteert verzoeken tot 512 MB (bedoeld voor een restore-upload die niet bestaat), en de API leest die in vóór de inlogcontrole. Eén verzoek van ±80–100 MB laat de API-container crashen.

Eerlijk gezegd: wachtwoorden zijn minimaal 12 tekens en Argon2 is traag, dus raden lukt alleen bij zwakke wachtwoorden. Cloudflare Access (stap 4 in `docs/buiten-huis.md`) houdt dit van internet weg. Op het thuisnetwerk blijft het mogelijk.
- **Oplossing:**
  - Vertrouw alleen nginx (`SHC_FORWARDED_ALLOW_IPS` = het interne netwerk). Gebruik in nginx `set_real_ip_from` + `real_ip_header CF-Connecting-IP` en overschrijf `X-Forwarded-For`.
  - Bouw een wachttijd per gebruikersnaam in (opgeslagen in de database) en begrens de lijst met pogingen.
  - Laat Argon2 in een aparte thread draaien, met maximaal 1–2 tegelijk, en geef anders direct een 429-antwoord.
  - Stel `client_max_body_size 1m` in en laat de API alles boven ±256 KB weigeren.
  - Zet `limit_req` in nginx voor inloggen en `/pairing/redeem`.
  - Gebruik de ongebruikte instelling `trusted_proxies`, of verwijder hem.
- **Inspanning:** S
- **Bestanden:** `infra/compose/compose.yaml`, `apps/web/nginx.conf.template`, `apps/api/src/shc_api/routers/auth.py`, `apps/api/src/shc_api/cli.py`, `apps/api/src/shc_api/security.py`

**17. De Android-app kan het toegangstoken onversleuteld naar het verkeerde adres sturen** _(SECU-2, ANDR-1, ANDR-2)_
De app kiest de "thuis"-route zodra iets op het opgeslagen thuisadres antwoordt met willekeurige JSON. Hij controleert niet of het jouw server is, onthoudt de route ook na het wisselen van wifi, en stuurt het Cloudflare Access-geheim bij elk verzoek mee.

Het meest realistische risico: is `SHC_LAN_URL` niet ingesteld en maak je de QR-code via de tunnel, dan vult de server als thuisadres `http://huis.jouwdomein.nl` in. Het token gaat dan onversleuteld over internet. Diezelfde terugval gaat thuis ook mis: het poortnummer ontbreekt.

Niet helemaal zeker hoe waarschijnlijk dit is. De handleiding zegt dat je `--lan-url` moet meegeven. Misbruik op een vreemd wifi-netwerk vereist een apparaat op precies hetzelfde IP-adres en dezelfde poort.
- **Oplossing:**
  - Snel (S): maak het thuisadres verplicht (geen terugval op het binnenkomende adres). Sta http alleen toe voor privé-adressen en `.local`. Weiger een remote-adres zonder https. Stuur Cloudflare-headers nooit naar het thuisadres. Vergeet de route bij elke netwerkwissel.
  - Beter (M): laat de app de server herkennen, via een server-ID met een controlevraag, of via een vastgezette certificaat-vingerafdruk in de QR-code.
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/routers/pairing.py`, `apps/android/core/src/main/kotlin/nl/huis/smarthome/core/Pairing.kt`, `apps/android/core/src/main/kotlin/nl/huis/smarthome/core/ApiClient.kt`, `apps/android/app/src/main/java/nl/huis/smarthome/data/HomeRepository.kt`, `apps/android/app/src/main/res/xml/network_security_config.xml`

**18. Een gekoppelde telefoon is voor altijd volledig beheerder, en gebruikers beheren kan niet** _(SECU-8, ANDR-7, CONT-8, PLAN-9)_
Een telefoontoken krijgt alle rechten van de beheerder die de QR-code maakte, en verloopt nooit. Daarmee kun je nieuwe koppelcodes en nieuwe beheerders maken en back-ups downloaden. Een dief kan zijn toegang dus vastleggen voordat jij de telefoon intrekt.

Verder:
- Gebruikers tonen, uitschakelen of verwijderen kan niet, en je wachtwoord wijzigen ook niet. Er is alleen een reset via de opdrachtregel, en die laat telefoontokens staan.
- Een ingetrokken telefoon blijft live-gegevens ontvangen zolang de verbinding open staat.
- "Ontkoppelen" in de app wist alleen de gegevens op de telefoon; het token blijft op de server geldig.
- Een ingetrokken telefoon toont "Geen verbinding" in plaats van "Opnieuw koppelen".
- Een kijk-account (viewer) kan "Terug naar schema" gebruiken, en dat wordt niet vastgelegd.
- Meerdere soorten wijzigingen komen niet in het auditlog, en dat log is nergens te bekijken.
- **Oplossing:**
  - Laat bij het koppelen een rol kiezen (standaard beperkt).
  - Blokkeer telefoontokens voor gebruikers-, koppel- en back-upfuncties, of vraag daarvoor het wachtwoord.
  - Laat tokens verlopen en vernieuwen.
  - Voeg gebruikersbeheer en wachtwoord wijzigen toe, waarbij sessies én tokens worden ingetrokken.
  - Sluit live-verbindingen bij intrekken of uitloggen.
  - Voeg `DELETE /clients/self` toe en roep dat aan bij ontkoppelen. Toon in Android een scherm "Deze telefoon is niet meer gekoppeld".
  - Maak "Terug naar schema" alleen voor beheerders, en schrijf alle wijzigingen naar het auditlog.
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/routers/pairing.py`, `apps/api/src/shc_api/deps.py`, `apps/api/src/shc_api/routers/auth.py`, `apps/api/src/shc_api/routers/ws.py`, `apps/api/src/shc_api/routers/zones.py`, `apps/android/app/src/main/java/nl/huis/smarthome/data/HomeRepository.kt`

---

## Binnenkort

**19. Energiepagina, export en database lopen vast naarmate de meetdata groeit** _(ENER-1, ENER-4, ENER-5, PLAN-4)_
Elke meting wordt voor altijd bewaard: ±100.000 rijen per dag, ±20 GB per jaar. Er zijn geen uur-, dag- of maandtotalen.

De energiepagina leest alle ruwe metingen en rekent ze door in een trage lus, op de enige hoofdlus van de API. Gemeten: één dag kost ±0,5 s per reeks (het overzicht doet dit elke minuut, samen ±2 s), één maand ±15 s per reeks. Het openen van de energiepagina (standaard op maand) bevriest de hele API dan ±1 minuut, en het jaaroverzicht laat de API crashen.

Een CSV-export bouwt het hele bestand in het geheugen en crasht na een week of twee aan data. Na het opnieuw toewijzen van een meter kunnen exportbestanden bovendien dubbele of verkeerd toegewezen rijen bevatten (zeldzaam). De klimaatregeling blijft wel werken, want die zit in de worker.
- **Oplossing:**
  - Snel: reken met één doorloop in plaats van een lus binnen een lus, en doe het in een aparte thread.
  - Structureel: maak een tabel met uurtotalen per meter (bijgewerkt door de worker) plus dag- en maandtotalen, en laat de pagina's daaruit lezen.
  - Maak de bewaartermijn instelbaar, bijvoorbeeld vermogen 30 dagen ruw, tellerstanden 1 jaar ruw en totalen voor altijd. Verwijder oude data in delen, of partitioneer per maand.
  - Export: stuur het bestand in stukjes (streamen), standaard met uur- of dagtotalen, en ruwe data alleen met een maximale periode. Filter op de periode waarin een meter aan een rol gekoppeld was, en schrijf lege waarden als lege cel. Excel is optioneel.
- **Inspanning:** L
- **Bestanden:** `apps/api/src/shc_api/energy.py`, `apps/api/src/shc_api/views.py`, `apps/api/src/shc_api/routers/energy.py`, `apps/api/src/shc_api/models.py`, `apps/api/src/shc_api/worker.py`

**20. Eén foute meterstand levert duizenden kWh extra op** _(ENER-2)_
Elke daling naar onder 5 kWh wordt gezien als "meter gereset", en de volgende normale stand telt dan als één enorme sprong. Eén foute 0 geeft ±13.779 kWh in één uur (meer dan € 4.000). Een negatieve foute waarde telt als negatief verbruik. Er is geen controle op aannemelijkheid en geen manier om het te corrigeren. Hoe vaak een P1 of stekker zulke foute waarden geeft is onbekend, maar één keer is genoeg om de totalen blijvend te bederven.
- **Oplossing:** Begrens elke stap op een maximaal aannemelijk vermogen per rol (P1 ±17 kW, Energy Socket 3,7 kW, instelbaar). Markeer overschrijdingen als "verdacht" en sla ze over. Accepteer een reset alleen als de volgende standen netjes oplopen vanaf de nieuwe basis. Weiger negatieve tellerstanden bij binnenkomst. Voeg deze gevallen als test toe, en toon verdachte periodes in de kwaliteitskolom.
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/energy.py`, `apps/api/src/shc_api/store.py`

**21. Zomer- en wintertijd geven spookverbruik en onterechte "onvolledig"-meldingen** _(ENER-6, ENER-7)_
Een P1 op API v2 geeft de lokale tijd zonder tijdzone. In het dubbele uur bij de overgang naar wintertijd raken de metingen door elkaar. In een simulatie leverde dat die dag ±335–350 kWh spookverbruik op. Volgende keer: eind oktober 2027.

Niet helemaal zeker: dit gaat uit van het testvoorbeeld. Controleer op je echte P1 of de tijd inderdaad zonder tijdzone binnenkomt.

Daarnaast, kleiner: de app rekent de duur van periodes in kloktijd. Daardoor verschijnt de overgang in maart elk jaar als "onvolledig/geschat" in het week-, maand- en jaaroverzicht. Een storing die het dubbele uur overspant, raakt één uur verbruik kwijt.
- **Oplossing:** Kies bij twijfel de tijd die het dichtst bij het moment van uitlezen (UTC) ligt, of gebruik dat moment zelf als het verschil te groot is. Reken alle periodes in UTC. Voeg tests toe voor beide overgangsdagen.
- **Inspanning:** S
- **Bestanden:** `integrations/src/shc_integrations/homewizard/adapter.py`, `apps/api/src/shc_api/energy.py`, `apps/api/src/shc_api/views.py`

**22. Na een herstel op een nieuwe NAS (of na een sleutelwissel) blijft de worker crashen, terwijl het dashboard groen blijft** _(CRIT-1)_
Bij het opstarten ontsleutelt de worker de opgeslagen apparaatsleutels. Past de hoofdsleutel niet (nieuwe server, of de sleutelwissel uit `docs/onderhoud.md`), dan stopt de worker meteen en herstart eindeloos. Dan is er geen klimaatregeling, geen meting, geen back-up en geen melding meer. Het overzicht in web en Android blijft groen; alleen de beheerpagina Systeem toont de hartslag van de worker.

Opnieuw koppelen helpt niet, want de oude, onleesbare koppeling blijft actief. De handleiding vergeet ook te noemen dat de Gree-sleutel opnieuw moet.
- **Oplossing:**
  - Laad elke koppeling apart. Bij een fout: markeer "sleutel past niet, koppel opnieuw" en ga door met de rest.
  - Laat opnieuw koppelen de bestaande koppeling bijwerken (zie punt 24).
  - Voeg `shc-api rekey --old-key-file` toe, en laat restore controleren of de sleutels te ontsleutelen zijn.
  - Pas de handleiding aan.
  - Zet worker-hartslag, veilige modus en leeftijd van de laatste back-up ook in het gewone overzicht, met een melding als de hartslag wegvalt.
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/worker.py`, `apps/api/src/shc_api/security.py`, `apps/api/src/shc_api/views.py`, `apps/api/src/shc_api/routers/homes.py`, `docs/onderhoud.md`

**23. De web-app toont een commando als klaar voordat de airco iets heeft gedaan** _(WEB-3)_
De server stuurt direct na het commando al een zone-update met de oude waarden, en de web-app ziet élke zone-update als bevestiging. De doeltemperatuur springt dan terug, "wacht op bevestiging" verdwijnt binnen milliseconden, en twee keer snel op "+" drukken stuurt twee keer dezelfde waarde. De echte uitkomst (bevestigd, mislukt, of geweigerd met reden) wordt wel verstuurd, maar genegeerd; fouten zie je alleen in het logboek. Bij lampen kan "Verzonden" eeuwig blijven staan. Je ziet dit pas als de live-verbinding werkt (na punt 4).
- **Oplossing:** Onthoud het `command_id` uit het antwoord, en bevestig pas bij `command.updated` met status "confirmed". Toon anders "verzonden, niet bevestigd", en fouten met hun reden. Wacht bij +/− ±800 ms en stuur dan één eindwaarde. Verwerk het bericht `refresh`, en toon ook bij lampen een time-out.
- **Inspanning:** M
- **Bestanden:** `apps/web/src/ws.ts`, `apps/web/src/components/zone.tsx`, `apps/web/src/api/types.ts`, `apps/web/src/api/client.ts`, `apps/web/src/pages/Lights.tsx`, `apps/api/src/shc_api/routers/zones.py`

**24. Opnieuw koppelen, een IP-wissel of een woning archiveren laat oude koppelingen actief** _(ADAP-5, CRIT-5, PLAN-6)_
Elke koppelpoging maakt een nieuwe koppeling, en de oude blijft aan. Lampen, scènes en knoppen van de Hue Bridge blijven aan de oude koppeling hangen, dus hun commando's mislukken. Met twee actieve koppelingen wordt elke knopdruk twee keer verwerkt, waardoor aan/uit zichzelf opheft.

Verder:
- De worker stopt uitgeschakelde koppelingen nooit, ook niet bij het archiveren van een woning. Metingen blijven dan gewoon naar de oude woning gaan.
- Een HomeWizard (v1) wordt niet gecontroleerd op serienummer. Antwoordt na een DHCP-wissel een ander HomeWizard-apparaat op het oude IP-adres, dan komen zijn standen in de historie van de oude meter (spook-kWh).
- De controle op de buitenunit kijkt niet per woning.

Met vaste IP-adressen (DHCP-reservering) is dit minder waarschijnlijk. Maar opnieuw koppelen is de logische eerste stap bij problemen.
- **Oplossing:**
  - Laat koppelen per apparaat-identiteit de bestaande koppeling bijwerken in plaats van een nieuwe te maken.
  - Voeg een functie toe om alleen het adres te wijzigen.
  - Hang apparaten over naar de huidige koppeling, en laat de worker uitgeschakelde, gewijzigde en gearchiveerde koppelingen stoppen.
  - Ontdubbel knopgebeurtenissen.
  - Controleer het HomeWizard-serienummer bij het verbinden en daarna regelmatig, met de melding "apparaat op ander adres".
  - Beperk de buitenunit-controle tot de eigen woning.
  - Beschrijf een DHCP-reservering in de documentatie.
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/routers/devices.py`, `apps/api/src/shc_api/worker.py`, `apps/api/src/shc_api/routers/system.py`, `apps/api/src/shc_api/views.py`, `integrations/src/shc_integrations/homewizard/adapter.py`, `docs/onderhoud.md`

**25. Hue-knoppen kun je niet aan app-acties koppelen** _(ADAP-6, PLAN-1, WEB-5, CONT-10, CONT-11)_
Echte Hue-knop-ID's zijn 36 tekens lang, maar de database en de API staan er maar 32 toe. Elke koppeling wordt dus geweigerd. Er is ook geen editor: web en Android tonen alleen een lijst, er is geen simulatie, en wat de bridge zelf al met een knop doet wordt nooit ingelezen. Daardoor verschijnt de waarschuwing voor dubbele acties nooit.

Daarnaast:
- Knop-apparaten ontstaan pas bij de eerste druk, en heten allemaal "Hue-knop".
- Koppelingen worden niet gecontroleerd. Een "lampen"-koppeling kan een airco-stap bevatten; die slaat de buitenunit-controle over en wordt daarna voor de afstandsbediening aangezien.
- Gebeurtenissen worden niet ontdubbeld, en de reactie is ±5 s traag.

Je knoppen blijven gewoon werken via de bridge; deze functie is alleen nog niet bruikbaar.
- **Oplossing:**
  - Zet de kolom en de controle op 64 tekens (met een migratie).
  - Maak knop-apparaten aan bij het inlezen, met naam, model, knoppen en mogelijke gebeurtenissen.
  - Lees `behavior_instance` in en vul daarmee de bridge-regels.
  - Controleer de stappen bij het opslaan, en stuur airco-acties langs dezelfde weg als `POST /zones/{id}/command`.
  - Ontdubbel op `event_id`, en negeer gebeurtenissen die ouder zijn dan ±10 s.
  - Geef de wachtrij pas een seintje nadat de opdracht is opgeslagen (na de commit).
  - Bouw een editor met "Simuleer" en conflicten per regel, en test met echte, 36 tekens lange ID's.
- **Inspanning:** L
- **Bestanden:** `apps/api/src/shc_api/models.py`, `apps/api/src/shc_api/routers/lights.py`, `apps/api/src/shc_api/worker.py`, `integrations/src/shc_integrations/hue/adapter.py`, `apps/web/src/pages/Lights.tsx`, `apps/web/src/api/client.ts`

**26. De verbinding voor Hue-gebeurtenissen kan voor altijd blijven hangen** _(ADAP-1)_
Die verbinding heeft geen leestime-out, en de bridge stuurt geen "ik leef nog"-berichten. Valt alleen de bridge stroomloos, verandert zijn IP-adres, of stopt de stroom berichten stilletjes, dan wacht de worker eindeloos. Knopacties werken dan niet meer, terwijl de status "ok" blijft (het uitlezen elke 120 s werkt nog wel). Een gewone firmware-herstart wordt meestal wel netjes opgevangen.
- **Oplossing:** Stel een leestime-out van 90–120 s in, met een waakhond die opnieuw verbindt als er te lang niets binnenkomt, en TCP-keepalive. Lees na het opnieuw verbinden alles opnieuw in. Toon "verbonden sinds" en geef een melding bij uitval.
- **Inspanning:** S
- **Bestanden:** `integrations/src/shc_integrations/hue/adapter.py`, `apps/api/src/shc_api/worker.py`

**27. De lichtpagina toont verkeerde gegevens** _(ADAP-7)_
- Elke kamer toont "0 van 0 aan".
- De actieve scène wordt nooit aangegeven.
- Er verschijnt een extra kamer "Lichtgroep" (de groep "alle lampen").
- Een lamp zonder stroom blijft "bereikbaar".
- Foutmeldingen van de bridge ("device has communication issues") worden genegeerd, waardoor een mislukt commando als bevestigd telt.

Niet gevaarlijk, maar de pagina oogt bij de eerste kennismaking kapot.
- **Oplossing:** Bereken per kamer hoeveel lampen aan staan van het totaal. Gebruik `scene.status.active` voor de actieve scène, en bepaal bereikbaarheid per lamp via het bijbehorende apparaat. Toon de groep "alle lampen" als "Alle lampen" of verberg hem. Behandel een gevulde `errors`-lijst als niet bevestigd.
- **Inspanning:** M
- **Bestanden:** `integrations/src/shc_integrations/hue/adapter.py`, `apps/api/src/shc_api/worker.py`, `apps/api/src/shc_api/views.py`, `apps/web/src/pages/Lights.tsx`

**28. Systeemstatus en meldingen zeggen weinig over de echte toestand** _(ADAP-9, PLAN-5, OPS-7, OPS-9, CRIT-7)_
Problemen worden gemist:
- De gezondheidscontrole van de koppelingen wordt nooit aangeroepen.
- De Zigbee-koppeling blijft voor altijd op "Wacht op verbinding met Zigbee2MQTT". Een losgetrokken dongle of een herstarte MQTT-server geeft geen melding, alleen indirect via "sensor verouderd" na 30 minuten.
- De worker heeft geen eigen healthcheck, dus een vastgelopen lus blijft "ok".
- De tijdcontrole vergelijkt twee containers die dezelfde klok gebruiken, en geeft dus altijd "ok".

Omgekeerd wordt er te snel alarm geslagen:
- Eén mislukte uitleespoging geeft meteen een rode melding, zet alle apparaten offline en stuurt een pushbericht. Bij korte wifi-haperingen knippert de status daardoor steeds.
- Elke mislukte poging vult de logs met een volledige foutmelding.

En mislukt een migratie, dan krijg je een kale 502-fout zonder uitleg.
- **Oplossing:**
  - Roep per koppeling de gezondheidscontrole aan en baseer status en meldingen daarop. Toon de MQTT-verbinding en de dongle (bridge online/offline) apart.
  - Zet pas op "fout" en meld pas na N mislukte pogingen of X minuten. Log één regel per statuswissel.
  - Laat de worker per lus een tijdstempel schrijven, en zichzelf herstarten als een lus stilstaat.
  - Laat de web-container niet wachten op een "gezonde" API, en toon een pagina "API start of migreert".
  - Vergelijk de klok met een echte NTP-bron.
  - Optioneel: een pushbericht buiten de app om (bijvoorbeeld ntfy) als de worker of de back-up stilvalt.
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/worker.py`, `integrations/src/shc_integrations/zigbee/adapter.py`, `apps/api/src/shc_api/routers/system.py`, `apps/api/src/shc_api/views.py`, `infra/compose/compose.yaml`, `apps/web/nginx.conf.template`

**29. De app vraagt apparaten nooit wat ze kunnen, en een mislukte Gree-koppeling laat geen spoor na** _(ADAP-9, PLAN-7)_
De mogelijkheden van een apparaat (modi, minimale en maximale temperatuur, stapgrootte, ventilator, swing) worden nooit opgeslagen; de app gebruikt vaste standaardwaarden. Mislukt het koppelen van een Gree-unit, dan geeft de API alleen een foutcode. Er wordt geen diagnostiek bewaard en geen status "niet ondersteund"; de status blijft altijd "ongetest". Voor jouw ongeteste Gree-units betekent dit: gaat het mis, dan heb je niets om naar support te sturen of om zelf mee uit te zoeken.
- **Oplossing:** Bewaar bij een mislukte koppeling een apparaat of diagnostiekrecord met de zoekgegevens (model, versie), en toon een diagnostiekblok dat je kunt kopiëren. Voeg een handmatige "getest"-bevestiging toe. Schrijf na het koppelen en bij elke uitleesronde de mogelijkheden naar het apparaat. Toon ventilator en swing alleen als het apparaat ze heeft. Toon de compatibiliteitstabel in de app.
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/routers/devices.py`, `integrations/src/shc_integrations/gree/adapter.py`, `apps/api/src/shc_api/views.py`, `docs/compatibility.md`

**30. Bijsturen met de externe sensor werkt nauwelijks** _(CONT-5)_
Omdat de stapgrootte nooit wordt ingevuld (zie punt 29), rekent de app met hele graden. De correctie schuift maximaal 0,5 °C per keer, en de afronding ("naar even") slikt dat in. Bij 20 of 22 °C gebeurt er daardoor nooit iets, en bij oneven doelen blijft de correctie één graad te hoog hangen.

Niet helemaal zeker hoe erg dit is. Zolang er geen sensoren zijn (nog geen dongle), gebruikt de airco gewoon zijn eigen thermostaat. Er gaat dus niets mis; het bijsturen werkt alleen niet.
- **Oplossing:** Bewaar de correctie met decimalen, los van de waarde die naar de unit gaat. Schuif minstens één stap in de richting van de afwijking. Vul de stapgrootte vanuit de adapter. Voeg tests toe met stap 1,0 en met even én oneven doelen.
- **Inspanning:** S
- **Bestanden:** `apps/api/src/shc_api/climate.py`, `apps/api/src/shc_api/views.py`, `apps/api/tests/test_core_logic.py`

**31. Zones zonder gekoppelde airco tonen een rode foutmelding** _(WEB-6)_
Een nieuwe woning krijgt altijd de zones "Woonkamer" en "Slaapkamer", zonder airco. De app toont dan "Airco-unit offline" in rood en schakelt de bediening uit, terwijl er niets kapot is. Zolang je Gree-units nog niet gekoppeld zijn, is je dashboard dus permanent rood. Het koppelen zit onderaan de pagina Klimaat, terwijl de teksten naar Apparaten verwijzen.
- **Oplossing:** Maak onderscheid tussen "nog geen airco gekoppeld" (neutrale tekst + knop om te koppelen) en "offline". Toon bij een zone met alleen een sensor de sensorwaarden. Pas de teksten aan.
- **Inspanning:** S
- **Bestanden:** `apps/api/src/shc_api/views.py`, `apps/web/src/components/zone.tsx`, `apps/web/src/pages/Climate.tsx`, `apps/web/src/pages/Overview.tsx`

**32. Automatische back-ups staan standaard uit, en de bewaartermijn is niet in te stellen** _(PLAN-11, OPS-12)_
Automatische back-ups draaien pas als je handmatig een wachtwoordbestand op de NAS aanmaakt. Verder:
- Hoe lang back-ups bewaard worden, is niet in te stellen.
- Het opruimen verwijdert ook handmatige back-ups en back-ups van vóór een update. Ze blijven wel in de lijst staan, maar downloaden geeft dan een 404-fout.
- Er is maar één moment (03:00). Staat de NAS dan uit, dan wordt die dag overgeslagen.
- Een mislukte back-up geeft geen melding (de status waarschuwt wel na 36 uur).
- Het wachtwoordbestand staat op dezelfde dataset als de back-ups.

Positief: elke back-up wordt na het schrijven al ontsleuteld en gecontroleerd.
- **Oplossing:**
  - Laat de beheerder de wachtwoordzin, het tijdstip en de bewaartermijn in de app instellen (versleuteld opgeslagen).
  - Ruim alleen automatische back-ups op, inclusief hun regel in de lijst.
  - Maak de back-up bij de eerste gelegenheid na 03:00 als de vorige ouder is dan 23 uur, en geef een melding bij een fout.
  - Overweeg dagelijks een database-dump en wekelijks de volledige export.
  - Kopieer bij replicatie alleen de map `backups/`, zonder de wachtwoordzin.

  Doe dit samen met punt 5.
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/worker.py`, `apps/api/src/shc_api/routers/system.py`, `docs/onderhoud.md`

**33. Zigbee2MQTT-gegevens komen stilletjes niet in de back-up** _(OPS-2)_
`install.sh` maakt de map `zigbee2mqtt` zo aan dat alleen root hem kan lezen. De back-up draait als een andere gebruiker en slaat de map zonder melding over. De netwerksleutel en de apparatenlijst ontbreken dus, terwijl de handleiding belooft dat ze erin zitten. Maak je de map leesbaar maar het configuratiebestand niet, dan mislukt juist elke back-up. Nu is de impact nog klein (geen dongle; twee sensoren opnieuw koppelen gaat snel), en op TrueNAS kunnen ACL's het gedrag veranderen.
- **Oplossing:** Maak de map aan met groep 10001 (`install -d -m 2750 -g 10001`) en de bestanden met rechten 0640. Geef een fout of melding als de map wel gekoppeld maar niet leesbaar is, en toon "Zigbee in laatste back-up: ja/nee". Test als gebruiker zonder root-rechten.
- **Inspanning:** S
- **Bestanden:** `infra/scripts/install.sh`, `apps/api/src/shc_api/backup.py`

**34. Herstellen controleert de versie niet, en updaten maakt geen automatisch herstelpunt** _(CRIT-6, OPS-6)_
Bij herstellen wordt niet gekeken met welke app- en databaseversie een back-up is gemaakt. Stel: je gaat na een update terug naar de oude versie en herstelt een nachtelijke back-up van de nieuwe versie. Dan vallen nieuwe kolommen en tabellen stilletjes weg, terwijl er "Hersteld" staat. Andersom kan het herstel mislukken met een onduidelijke databasefout.

Verder:
- Bij een update wordt de database omgezet (gemigreerd) zonder automatische dump vooraf. De handleiding vraagt wel om eerst handmatig een back-up te maken.
- De hersteloefening in de handleiding maakt een nieuwe hoofdsleutel aan, en test daardoor juist níet of je bewaarde sleutel werkt.
- Kleiner: herstellen draait terwijl de worker doorloopt, en een fout tijdens het migreren bij herstellen wordt ingeslikt (in de praktijk weinig waarschijnlijk).
- **Oplossing:**
  - Sla bij elke back-up de app-versie en de schemaversie op, en toon die.
  - Weiger een back-up van een nieuwere versie. Herstel een oudere back-up eerst in zijn eigen versie en migreer daarna vooruit. Noem expliciet wat er wegvalt.
  - Maak vóór elke migratie automatisch een `pg_dump`, en geef migratiefouten door.
  - Weiger herstellen zolang de worker draait, of pauzeer hem.
  - Voeg een commando `restore-drill` toe dat in een tijdelijke database herstelt, de aantallen vergelijkt en de apparaatsleutels probeert te ontsleutelen.
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/backup.py`, `apps/api/src/shc_api/cli.py`, `apps/api/src/shc_api/migrations/env.py`, `docs/onderhoud.md`

**35. De kosten kloppen niet met je energierekening** _(ENER-10)_
- Vaste kosten per dag worden nooit meegeteld.
- Een tariefwijziging midden in de maand wordt in het jaaroverzicht genegeerd: de hele maand krijgt het tarief van de 1e.
- Eén periode zonder tarief maakt de kosten van de hele meter leeg. Het formulier zet "geldig vanaf" standaard op vandaag, dus week, maand en jaar tonen geen kosten tot je die datum terugzet.
- Tarieven kun je niet wijzigen of verwijderen, en bij twee tarieven met dezelfde datum wordt willekeurig er één gekozen.
- Er is maar één prijs, terwijl veel contracten een dag- en nachttarief (T1/T2) hebben.
- **Oplossing:** Laad de tarieven één keer en splits periodes op de ingangsdata (reken per uur). Tel vaste kosten × aantal dagen op. Toon gedeeltelijke kosten, met hoeveel van de periode gedekt is, in plaats van niets. Voeg een unieke sleutel (woning + datum) toe, plus wijzigen en verwijderen (vastgelegd in het auditlog). Optioneel: aparte prijzen voor T1/T2.
- **Inspanning:** M
- **Bestanden:** `apps/api/src/shc_api/views.py`, `apps/api/src/shc_api/routers/energy.py`, `apps/api/src/shc_api/models.py`, `apps/web/src/pages/Energy.tsx`

**36. Live-berichten die de documentatie belooft, worden nooit verstuurd** _(CRIT-3, ENER-9)_
`docs/api.md` belooft de berichten `energy.live` (actueel vermogen) en `status.updated`. Web en Android luisteren ernaar, maar de server stuurt ze nooit. Vermogen en status op de telefoon zijn daardoor tot 2 minuten oud, terwijl Android "tot <tijd>" toont alsof de gegevens verser zijn. Opgeloste meldingen blijven zichtbaar, omdat er geen "opgelost"-bericht bestaat. Het bericht `refresh` wordt door geen van beide apps verwerkt. En de documentatie noemt de rol `grid_import`, terwijl de code `grid` gebruikt.
- **Oplossing:** Stuur `energy.live` na een HomeWizard-uitlezing (maximaal elke 5–10 s), `status.updated` bij een statuswissel en `alert.resolved` bij het oplossen van een melding. Laat beide apps bij `refresh` alles opnieuw ophalen. Leg de berichttypes vast in één gedeelde beschrijving, en test dat elk gedocumenteerd bericht ook echt verstuurd wordt. Gebruik in code en documentatie dezelfde rolnaam.
- **Inspanning:** S
- **Bestanden:** `apps/api/src/shc_api/worker.py`, `apps/api/src/shc_api/store.py`, `apps/api/src/shc_api/events.py`, `docs/api.md`, `apps/web/src/ws.ts`, `apps/android/core/src/main/kotlin/nl/huis/smarthome/core/Realtime.kt`

**37. Android: geen meldingen onderweg, en de app wisselt niet goed tussen thuis en onderweg** _(ANDR-4, ANDR-5, ANDR-6)_
De achtergrondcontrole voor meldingen blijft de laatst gebruikte route gebruiken. Ga je van huis met de app op de achtergrond, dan probeert hij elke 15 minuten het thuisadres via mobiele data. Dat mislukt, en hij probeert precies hetzelfde opnieuw. Je krijgt dus geen meldingen juist als je weg bent. Dit speelt pas zodra de tunnel bestaat.

Verder:
- Eenmaal op de route "onderweg" schakelt de app thuis niet terug naar het thuisnetwerk.
- De adressen liggen vast vanaf het koppelen. Krijgt de NAS een nieuw IP-adres, dan moet je opnieuw koppelen.
- Een serverfout (5xx, Cloudflare 530) houdt de status op "online", met elke 20 s een foutmelding in plaats van de offline-balk.

De knop "Opnieuw proberen" helpt wel.
- **Oplossing:**
  - Maak één hulpfunctie die bij een netwerkfout of 5xx de route vergeet en één keer opnieuw kiest, ook in de achtergrondcontrole.
  - Vergeet de route bij elke netwerkwissel (NetworkCallback), en probeer bij het openen eerst het thuisadres.
  - Haal na het verbinden de actuele adressen op via `/system/settings`, en voeg "Adressen bewerken" toe onder Meer.
  - Behandel 5xx en Cloudflare-foutpagina's als offline, en toon een foutmelding alleen bij een statuswissel.
- **Inspanning:** M
- **Bestanden:** `apps/android/app/src/main/java/nl/huis/smarthome/work/AlertWorker.kt`, `apps/android/app/src/main/java/nl/huis/smarthome/data/HomeRepository.kt`, `apps/android/app/src/main/java/nl/huis/smarthome/ui/screens/MoreScreen.kt`

**38. De web-app als telefoon-app (PWA): thuis niet te installeren, en na afloop van een Cloudflare Access-sessie lijkt hij permanent offline** _(CRIT-2)_
Thuis draait alles via gewone http, en een PWA vereist https. Thuis kun je hem dus niet installeren. Installeer je hem via het tunneladres, dan gaat ook thuis alles via internet.

Verloopt de Cloudflare Access-sessie (standaard na 24 uur), dan krijgt de app een doorverwijzing naar de Access-inlogpagina die hij niet begrijpt. Omdat de pagina uit de cache komt, verschijnt die inlogpagina nooit, en blijft de app "Server niet bereikbaar" zeggen tot je de sitegegevens wist. Je gebruikt vooral de Android-app, dus de impact is beperkt.
- **Oplossing:** Herken een doorverwijzing of een antwoord dat geen JSON is op `/api`, en laad dan de pagina volledig opnieuw buiten de cache om (of ga naar `/cdn-cgi/access/login`). Laad pagina's eerst via het netwerk, met de cache als terugval. Beschrijf de sessieduur in de documentatie. Voor https thuis: laat het tunneladres thuis naar de NAS wijzen (split-horizon DNS), met een certificaat. Dan werkt één https-adres zowel thuis als onderweg.
- **Inspanning:** M
- **Bestanden:** `apps/web/vite.config.ts`, `apps/web/src/api/client.ts`, `apps/web/nginx.conf.template`, `docs/buiten-huis.md`

**39. De energiepagina toont alleen de huidige periode, en "Overig" is bijna onzichtbaar** _(WEB-10)_
- Er zijn geen knoppen voor vorige en volgende periode, dus gisteren of vorige maand kun je niet bekijken.
- De datum loopt niet door na middernacht, en gebruikt de tijdzone van de browser.
- De weektitel toont alleen de begindag.
- De kleur van "Overig" heeft op wit maar 1,6:1 contrast (minimaal 3:1 is nodig), terwijl dat vaak het grootste deel van het verbruik is.
- De exportknoppen werken ook offline; je downloadt dan een foutbestand.
- Tarieven kun je alleen toevoegen.
- **Oplossing:** Voeg knoppen voor vorige/volgende en een datumkiezer toe (in de tijdzone van de woning), en een weektitel als "3–9 nov". Geef "Overig" een donkerdere kleur of arcering. Schakel export uit als de app offline is. Tarieven wijzigen en verwijderen hoort bij punt 35.
- **Inspanning:** S
- **Bestanden:** `apps/web/src/pages/Energy.tsx`, `apps/web/src/styles.css`

**40. Op de telefoon worden de knoppen van de thermostaat piepklein, en je kunt niet uitloggen** _(WEB-11)_
Getest op schermen van 320–412 px breed: de knoppen + en − van de thermostaat worden samengedrukt tot 19–37 px breed, te klein om goed te raken. Onder 820 px breed is de uitlogknop verborgen, en de 7 menu-items nemen 2–3 regels in beslag. (De pagina scrollt niet horizontaal, zoals eerst gedacht.)
- **Oplossing:** Laat de draaiknop meeschalen (`min(200px, 50vw)`) en maak de +/−-knoppen minstens 44–48 px. Zet uitloggen ook op de pagina Systeem of in een mobiel menu, en overweeg een balk onderaan met 4–5 hoofditems. Gebruik `minmax(min(300px,100%),1fr)` en toon apparaten op mobiel als kaarten.
- **Inspanning:** S
- **Bestanden:** `apps/web/src/pages/Climate.tsx`, `apps/web/src/styles.css`, `apps/web/src/components/Layout.tsx`

**41. Verouderde images, geen automatische updates of controles, en een release die niet op de tests wacht** _(OPS-10, SECU-10, OPS-8)_
De images zijn van eind 2024:
- cloudflared 2024.12.2, met automatisch bijwerken uit, valt buiten de ondersteuning van Cloudflare en kan zomaar stoppen met verbinden.
- Zigbee2MQTT 2.0.0 ondersteunt mogelijk de firmware niet van een dongle die je nu koopt.

Er wordt ook niets automatisch bijgehouden of gecontroleerd:
- Er is geen Dependabot of Renovate, geen kwetsbaarheidsscan, geen lockfile en geen ondertekening van images.
- GitHub Actions zijn op een tag vastgezet, terwijl ze schrijfrechten hebben.
- Het tunneltoken staat als gewone omgevingsvariabele in de configuratie.

En het releaseproces is los:
- De release publiceert images, ook als de tests falen.
- De images zijn in CI nooit samen als stack gestart.
- Het versienummer staat op zeven plekken; na een versie-ophoging zegt `/health` nog steeds 0.1.0.
- **Oplossing:**
  - Werk in elk geval cloudflared bij vóór je de tunnel aanzet, en Zigbee2MQTT vóór je de dongle aansluit. Geef het token door als bestand (`--token-file`).
  - Daarna: Renovate of Dependabot, een lockfile met hashes, Trivy/pip-audit/npm audit in CI, provenance/SBOM/cosign, en actions vastgezet op commit-SHA met rechten per job.
  - Laat de release wachten op CI, en voeg een rooktest toe: `docker compose up --wait`, health opvragen, een back-up maken en controleren.
  - Haal het versienummer uit één plek.
- **Inspanning:** M
- **Bestanden:** `infra/compose/compose.yaml`, `.github/workflows/release.yml`, `.github/workflows/ci.yml`, `apps/api/pyproject.toml`, `infra/scripts/render_truenas.py`

**42. Belangrijke onderdelen zijn niet getest** _(CONT-12, PLAN-12)_
De lus van de worker (klimaatronde, knoppen, gebeurtenissen, gezondheidscontrole) heeft geen tests, en juist daar zitten de fouten uit de punten 8, 10, 11, 13 en 14. De bestaande tests van de regeling gebruiken instellingen die fouten verbergen: stap 0,5, modus altijd "verwarmen", blokken altijd mét modus. Er zijn geen browsertests. Uitvalscenario's (MQTT, USB of PostgreSQL herstarten) zijn niet uitgeprobeerd, en er is geen standaard testset voor nieuwe adapters. Omdat er tot nu toe geen echte hardware is getest, komen deze fouten anders pas thuis boven.
- **Oplossing:** Begin met databasetests voor de worker, met nep-adapters. Test in elk geval:
  - twee zones op één buitenunit;
  - een mislukt commando, gevolgd door een nieuwe ronde;
  - een override die over het verlopen heen loopt;
  - stap 1,0 met even en oneven doelen;
  - schemablokken zonder modus;
  - een knop met een airco-actie;
  - een kijk-account dat "Terug naar schema" aanroept.

  Voeg daarna Playwright-browsertests toe tegen de compose-stack, en een contract-testset voor alle adapters.
- **Inspanning:** L (alleen de worker-tests: M)
- **Bestanden:** `apps/api/tests/`, `apps/api/tests/test_core_logic.py`, `integrations/tests/test_registry.py`, `docs/testlog.md`

---

## Later / nice to have

**43. Installatiewizard, verhuisassistent en accountbeheer in de app** _(WEB-8, PLAN-10, PLAN-6)_
Plan §7 vraagt om een installatiewizard: eerst controles (USB-pad, netwerkdetectie, klok), daarna Hue, HomeWizard, Gree, Zigbee, sensoren en meters. Het plan vraagt ook om een verhuisassistent. Wat er nu ontbreekt:
- Er zijn alleen losse koppelkaarten. Gree-instructies worden vóór het koppelen niet getoond, en er is geen hulppagina.
- Herstellen kan alleen via de opdrachtregel, en overschrijft dan alles.
- Een tekst in de app verwijst naar "Systeem › Verhuisassistent", dat niet bestaat. `docs/onderhoud.md` belooft functies die er niet zijn.
- Apparaten verwijderen of vervangen kan niet, en er is geen testknop per apparaat (terwijl de kaart voor de veilige modus zegt: "test ze").
- Er is geen accountpagina, en een kijk-account ziet knoppen die altijd een fout geven.
- **Oplossing:**
  - Maak een route `/installatie` met stappen en een API voor de controles vooraf.
  - Zet instructies op elke koppelkaart en maak een hulppagina.
  - Maak herstellen via upload mogelijk: eerst controleren, dan herstellen in veilige modus, met keuze A/B, apparaten herkennen op identiteit en een vergelijking van wat ze kunnen.
  - Voeg toe: apparaat verwijderen/vervangen, een testknop per apparaat en een accountpagina.
  - Verberg de bediening voor kijk-accounts.

  De foute verwijzingen kun je nu al aanpassen.
- **Inspanning:** L
- **Bestanden:** `apps/web/src/App.tsx`, `apps/web/src/pages/Devices.tsx`, `apps/web/src/pages/System.tsx`, `apps/api/src/shc_api/policy.py`, `docs/onderhoud.md`

**44. De certificaatcontrole voor Hue en HomeWizard gebeurt pas nadat het geheim al verstuurd is** _(ADAP-4, SECU-5)_
De vingerafdruk van het certificaat wordt pas gecontroleerd nadat het verzoek, met de Hue-sleutel of het HomeWizard-token erin, al verstuurd is. Een apparaat dat het IP-adres van de bridge of meter overneemt, krijgt het geheim dus als eerste. Kan de controle niet draaien, dan wordt hij overgeslagen.

Niet helemaal zeker hoe ernstig dit is. Het vereist een aanvaller op je thuisnetwerk, of een ander https-apparaat op een hergebruikt IP-adres. Je boilerstekker gebruikt sowieso de oude API zonder token.
- **Oplossing:** Controleer tijdens de TLS-handshake, vóór er iets verstuurd wordt. Gebruik bijvoorbeeld een SSL-context met alleen het vastgezette certificaat, of de CA van de fabrikant plus een controle op bridge-ID of serienummer. Behandel ontbrekende certificaatinformatie als fout.
- **Inspanning:** M
- **Bestanden:** `integrations/src/shc_integrations/tls.py`

**45. Comfortprofielen, waakhond en experimenten volgens het plan** _(PLAN-2, PLAN-8)_
"Profiel" is nu alleen een label. Er zijn geen echte comfortprofielen, geen energieprioriteit, geen waakhond (een melding als de kamer niet reageert) en geen instelbare grenzen voor sensorwaarden. Experimenten slaan alleen tijdvakken op en passen geen profielen toe. De rol "buitentemperatuur" kun je in de app niet kiezen, dus de uitkomst is altijd "indicatief" (je hebt ook nog geen buitensensor). Er is geen comfortscore en geen automatische pauze.
- **Oplossing:**
  - Maak profielen een echt onderdeel (doel, band, modus, schema) dat je kunt wisselen en toepassen.
  - Voeg energieprioriteit en een waakhond toe aan beleid, API en app.
  - Laat een experimentvariant verwijzen naar een profiel of schema, dat de worker automatisch aan- en uitzet.
  - Bied "buitentemperatuur" aan in de rolkeuze, of een optionele weerbron.
  - Neem de andere zone mee als storende factor, en voeg een comfortscore en een automatische pauze toe.
- **Inspanning:** L
- **Bestanden:** `apps/api/src/shc_api/models.py`, `apps/api/src/shc_api/routers/zones.py`, `apps/api/src/shc_api/routers/optimization.py`, `apps/api/src/shc_api/adaptive.py`, `apps/web/src/api/types.ts`

---

## Kleine punten

In deze lijst staat `…/` voor `apps/android/app/src/main/java/nl/huis/smarthome/`.

- **Geheimen zijn op de NAS voor iedereen leesbaar.** `install.sh` zet de geheimen op 0444 in een map met 0755. Het tunneltoken staat in de gegenereerde YAML, en een "geredigeerde" back-up bevat nog wachtwoord-hashes. Het TrueNAS-preset *Apps* (met ACL's) kan bovendien botsen met de chmod/chown in `install.sh`; dat is niet bevestigd. Oplossing: geef elk geheim de juiste eigenaar met rechten 0400/0440 en de map 0700, en adviseer of controleer het preset *Generic*. _(SECU-7, OPS-11)_ `infra/scripts/install.sh`, `infra/scripts/render_truenas.py`, `apps/api/src/shc_api/backup.py`
- **Alle containers zitten in één netwerk.** Via het Cloudflare-dashboard kan de tunnel direct naar de database of MQTT wijzen. Er is geen `cap_drop`, en Zigbee2MQTT draait als root. Oplossing: aparte netwerken (edge/app/data), `cap_drop: [ALL]`, en Zigbee2MQTT als gewone gebruiker. _(SECU-11)_ `infra/compose/compose.yaml`
- **Te ruime rechten.** De controle van het adres bij koppelen laat interne namen en loopback toe (alleen voor beheerders) en toont de ruwe fouttekst. De MQTT-ACL geeft meer rechten dan nodig. De API-documentatie (`/api/v1/docs`) is zonder inloggen te zien, ook via de tunnel. _(SECU-12)_ `apps/api/src/shc_api/routers/devices.py`, `infra/compose/config/acl`, `apps/api/src/shc_api/main.py`
- **Verbruik verdwijnt aan de rand van een dag** na een storing van meer dan ±4 uur over middernacht. Oplossing: haal per meter ook de laatste stand vóór en de eerste stand na de periode op. _(ENER-8)_ `apps/api/src/shc_api/views.py`
- **"Overig huis" en het live-vermogen** verbergen tijdelijk ontbrekende gegevens van tussenmeters (afronding naar 0; verouderde delen worden stil weggelaten). _(ENER-9)_ `apps/api/src/shc_api/views.py`
- **Gas** wordt opgeslagen, maar nergens getoond of berekend. Alleen relevant als je gas hebt. _(ENER-12)_ `apps/api/src/shc_api/views.py`, `apps/api/src/shc_api/models.py`
- **De Gree-uitlezing wijkt af van het protocol.** Een kamertemperatuur van 0 wordt getoond als 0 °C (bij een zone zonder sensor), TemRec wordt gelezen als +0,5 °C, en een vaste lamelstand als "swing". `docs/compatibility.md` herhaalt de fout. _(ADAP-8)_ `integrations/src/shc_integrations/gree/adapter.py`, `docs/compatibility.md`
- **Hue-scènes.** Bij elke gebeurtenis volgt een reeks losse databasevragen. Hernoemde of verwijderde scènes, lampen en kamers worden niet bijgewerkt. _(ADAP-11)_ `apps/api/src/shc_api/worker.py`
- **De rolkeuze op Apparaten** koppelt een meter direct opnieuw, zonder bevestiging en bij elk apparaattype. Een rol loskoppelen kan niet. De boiler blijft wel beschermd. _(WEB-7)_ `apps/web/src/pages/Devices.tsx`
- **De web-app onthoudt "laatst bekend" alleen zolang het tabblad open is.** Open je hem opnieuw zonder verbinding, dan zie je niets. _(WEB-9)_ `apps/web/src/App.tsx`
- **Toegankelijkheid web.** De helderheidsschuif stuurt al een commando als je er met Tab op komt, en bij elke pijltoets. Randen van invoervelden hebben te weinig contrast (1,5:1). De tabs zijn niet goed opgebouwd voor schermlezers. Er staan Engelse of ruwe woorden ("Override", "initial_press", "homewizard"). _(WEB-12)_ `apps/web/src/pages/Lights.tsx`, `apps/web/src/styles.css`
- **Android-meldingen.** Toestemming wordt bij elke start zonder uitleg gevraagd, en er is geen knop naar de instellingen. Meldingen die binnenkwamen zonder toestemming worden later niet meer getoond. Opgeloste meldingen blijven in de meldingenbalk staan. _(ANDR-8)_ `…/ui/HuisRoot.kt`, `…/work/AlertWorker.kt`
- **De Android-widget** ververst alleen als de app open is, en kan dag-oude waarden tonen alsof ze actueel zijn. Oplossing: laat de achtergrondtaak het overzicht ophalen. _(ANDR-9)_ `…/widget/HomeWidget.kt`
- **Android-sleutelopslag.** Een (zeldzame) fout in de beveiligde opslag bij het opstarten laat app, widget en achtergrondtaak steeds crashen. _(ANDR-10)_ `…/HuisApp.kt`, `…/data/SecureStore.kt`
- **Toegankelijkheid Android.** TalkBack meldt de nieuwe doeltemperatuur niet, en lamptegels hebben geen schakelaar-rol. Vaste maten knippen tekst af bij grote letters, het toetsenbord bedekt de Cloudflare-velden, en er staan losse teksten in de code. _(ANDR-11)_ `…/ui/screens/HomeScreen.kt`, `…/ui/screens/ClimateScreen.kt`, `…/ui/screens/MoreScreen.kt`
- **Android QR-scanner en Energie-tab.** Na "niet meer vragen" doet "Camera toestaan" niets. Dezelfde ongeldige QR-code wordt elke 3 s opnieuw ingediend. Een camerafout geeft een leeg scherm. De Energie-tab ververst niet zolang hij open is. Handmatig invoeren werkt wel. _(ANDR-12)_ `…/ui/screens/PairScreen.kt`, `…/ui/screens/EnergyScreen.kt`
- **Het aanpassingslog staat vol ruis.** De regeling schrijft elke minuut regels en stuurt elke keer een update (44 regels per uur bij 3 echte commando's). Oplossing: log alleen bij een andere beslissing, en voeg opruimen en een index toe. _(CRIT-8)_ `apps/api/src/shc_api/climate.py`, `apps/api/src/shc_api/worker.py`
