# Compatibiliteit en ondersteunde apparaten

**Status van alle regels hieronder: nog niet getest met echte hardware.**
De adapters zijn gebouwd volgens de openbare documentatie van de fabrikanten en getest met
contracttests op basis van die documentatie (zie `integrations/tests/fixtures/README.md` en
"Hoe adapters getest worden" onderaan). Een regel gaat pas naar "getest" nadat hij met een echt
apparaat in dit huis is gecontroleerd; noteer dan datum, firmware en wie het testte.

| Fabrikant | Model / producttype | API / protocol | Lokaal in de app | Eerst nodig in fabrikant-app | Status |
|---|---|---|---|---|---|
| Philips Hue | Bridge v2 (BSB002) | CLIP API v2 (HTTPS, eventstream) | Lampen, groepen (aan/totaal, actieve scène, bereikbaarheid), scènes, knop- en draai-events, knoppen met hun mogelijke events en wat de bridge er zelf al mee doet | Lampen/knoppen toevoegen aan de bridge (Hue-app) | nog niet getest met echte hardware |
| Philips Hue | Bridge v1 (rond) | — | Niet ondersteund (geen API v2) | — | niet ondersteund |
| HomeWizard | P1-meter (HWE-P1) | v1 (HTTP) en v2 (HTTPS + token) | Vermogen, kWh import/export per tarief, gas | ‘Lokale API’ aan in Energy-app; v2: knop op meter indrukken bij koppelen | nog niet getest met echte hardware |
| HomeWizard | Energy Socket (HWE-SKT) | v1 (HTTP); v2 bij HomeWizard nog ‘in ontwikkeling’ | Vermogen, kWh, stand van de schakelaar (`relay_on`) en ‘Switch lock’ (`switch_lock`). Schakelen alleen als het beleid het toestaat; **boiler-stekker nooit** | ‘Lokale API’ aan in Energy-app | nog niet getest met echte hardware |
| HomeWizard | kWh-meter 1-fase (HWE-KWH1) | v1 en v2 | Vermogen, kWh import/export | ‘Lokale API’ aan; v2: knop indrukken | nog niet getest met echte hardware (nog te bestellen) |
| HomeWizard | kWh-meter 3-fase (HWE-KWH3), SDM230-wifi, SDM630-wifi | v1 (SDM) / v1+v2 (KWH3) | Vermogen, kWh | ‘Lokale API’ aan | nog niet getest met echte hardware |
| Gree | Multisplit binnenunits (typenummer onbekend) | Lokaal UDP-protocol poort 7000, AES-ECB (v1) of AES-GCM (v2) | Aan/uit, modus, doeltemperatuur in **hele graden** (stap 1 °C; 0,5 °C alleen als `half_degrees` aan staat nadat dat met de unit is getest), ventilator, swing (alleen als de unit swing meldt), binnentemperatuur (TemSen; 0 = geen sensor) | Wifi-koppeling via Gree+-app op 2,4 GHz | nog niet getest met echte hardware — **compatibiliteitsgate**: binden + status lezen moet slagen, anders ‘niet ondersteund’ |
| Zigbee | Aqara WSDCGQ11LM (temp/vocht/druk) | Zigbee2MQTT via MQTT | Temperatuur, vochtigheid, druk, batterij, linkkwaliteit | Geen | nog niet getest met echte hardware |
| Zigbee | Coördinator: SONOFF ZBDongle-E (EFR32MG21, driver `ember`) of ZBDongle-P (CC2652P, driver `zstack`) | Zigbee2MQTT | Status: verbinding met MQTT en Zigbee2MQTT online/offline apart zichtbaar | Firmware volgens Zigbee2MQTT-adapterlijst | nog niet gekocht / nog niet getest |

## Detectie, protocol en terugval

De tabel hierboven geeft het protocol per producttype. De versie en firmware van een concreet apparaat
zijn onbekend totdat de koppeling ze heeft vastgesteld. Het LAN-adres van elk apparaat is hier onbekend.
Voor koppelingen op het LAN kan een DHCP-reservering adreswissels voorkomen. Leg bij de fysieke
proef alleen een gemaskeerde host vast in de compatibiliteitsmatrix.

| Koppeling | Detectie en vaststelling van de versie | Terugval als verbinden of bedienen uitvalt |
|---|---|---|
| Philips Hue | Zoek de bridge met mDNS. Voer het adres handmatig in als de bridge niet verschijnt. De bridge levert lampen, groepen, scènes en knoppen via CLIP API v2. | Gebruik de Hue-app of bestaande bediening op de bridge zolang die beschikbaar is. |
| HomeWizard | Zoek met mDNS of voer het adres handmatig in. De adapter stelt de API-versie vast bij koppelen en controleert de identiteit bij uitlezen. | Lees beschikbare waarden in de HomeWizard-app. Controleer bij een identiteitsfout eerst de DHCP-reservering en de koppeling. De boilerstekker blijft alleen voor meten. |
| Gree | Zoek via UDP-scan of voer het adres handmatig in. De werkende protocolversie volgt uit binden en teruglezen. Voor het eigen apparaat blijft dit onbekend tot D12. | Gebruik de originele afstandsbediening. De app laat de unit bij geweigerde klimaatsturing zelf regelen. |
| Zigbee | Start koppelen op de bridge. Zigbee2MQTT meldt het apparaat en vult de gegevens aan na het interview. De firmwareversie van het concrete apparaat is onbekend tot de fysieke controle. | Controleer MQTT en de coördinator afzonderlijk. Een alternatieve uitlezing voor een onbereikbare sensor is onbekend. |

`GET /api/v1/integrations/compatibility` geeft het protocol **per tabelrij**. Per bijpassend,
niet-gearchiveerd apparaat staan alleen `id`, `name`, `model`, `compatibility` en `tested_at` in het antwoord.
**Open onder P3-07** blijft het tonen van per-apparaatfirmware, protocolversie, gemaskeerde host,
detectiemethode en terugval. Migratie 0005 voegt `detection_method` (`scan` of `manual`) en `fallback`
toe als optionele velden. De app vult of toont die velden voor de matrix nog niet. Zie de
[deltamatrix](platform-v2/DELTA_MATRIX.md#stand-op-18-november-2026-s-p3-710).

## Apparaten in bedrijf stellen

Open **Apparaten → Apparaten beoordelen** voor de teststatus en de laatste bevestigde bediening per apparaat.
De opdrachthistorie helpt bij de beoordeling. Voer een leestest uit en controleer zelf of het apparaat werkt.
De leestest schakelt niets. Daarna kan een beheerder één apparaat of een uitdrukkelijk aangevinkte selectie
als getest vastleggen. Onder **Beheren → Testen → Testgeschiedenis** staan de eerdere bevestigingen met
tijd, uitvoerder, model en firmware voor zover die gegevens destijds zijn vastgelegd.

Bij een upgrade met bestaande apparaten begint de beveiliging uit. De beheerder beoordeelt de apparaten
en schakelt de beveiliging daarna bewust in voor alle woningen. De bevestiging toont hoeveel apparaten
daardoor niet meer kunnen worden bediend. Zodra de beveiliging aan staat, weigert de server ongeteste
bediening ook bij schema’s, knopkoppelingen en opdrachten die al wachten. De boiler blijft altijd alleen
meten. Bij een lege nieuwe installatie staat de beveiliging vanaf het begin aan.

De installatiewizard gaat van **Meters uitlezen** naar **Hue uitlezen** en daarna naar **Hue testen en bevestigen**.
Voor Gree wacht de wizard op een echte koppel- en terugleesproef voor de gebruikte airco en firmware (D12).
Met **Gree later instellen** kan de beheerder de overige stappen afmaken. Gebruik zolang de eigen
afstandsbediening voor Gree.

Open vóór een versiewissel **Terugval voorbereiden** als je naar een andere wizardindeling teruggaat.
Kies **Wizard met één Hue-stap** voor de versie met ‘Philips Hue’, of **Wizard met twee Hue-stappen**
voor de versie met ‘Hue uitlezen’ en ‘Hue testen en bevestigen’. Rechtstreekse terugval hervatte in de
proeven de opgeslagen stap en markeringen van de gekozen indeling in dezelfde browser. Ontbreekt zo'n
record, dan gebruikt de wizard de beschikbare voortgang.

Gemelde restbeperking bij een route via de nooit uitgebrachte tussencommits `098732a` en `d9ec105`:
bij het opnieuw openen van `bac9422` kan een nog intacte reservekopie worden overschreven. Bij terugval
kunnen de oorspronkelijke stap en markeringen daardoor verloren gaan. De regisseur heeft deze route
buiten de reikwijdte van het herstel verklaard.

Voor Hue blijft een eigen leestest en bevestiging nodig. Voor Gree geldt de fysieke D12-controle.

Bij een geweigerde bedienopdracht legt Android uit dat een beheerder het apparaat via de web-app moet
testen en bevestigen.

## Gree: wat de gate controleert

1. De unit antwoordt op een `scan` op UDP 7000. Model, MAC, firmware (`ver`), `mid`, merk en serie
   worden als diagnose bewaard.
2. Binden met de generieke sleutel. De app probeert **eerst de oude versleuteling (v1, AES-ECB) en
   daarna de nieuwe (v2, AES-GCM)**, net als de referentiebibliotheek. Het formaat van het
   scan-antwoord zegt namelijk niets: nieuwere modules antwoorden op de scan in het oude formaat
   maar willen binden met de nieuwe versleuteling. De versleuteling die werkt wordt onthouden.
3. Een statusverzoek met de eigen apparaatsleutel geeft geldige waarden terug.

Pas als alle drie slagen wordt de unit gekoppeld. Mislukt het, dan geeft de koppeling de foutcode
`unsupported` (of `device_offline` als de scan geen antwoord krijgt) met een diagnoseblok: het
scan-antwoord, welke versleutelingen geprobeerd zijn met de uitkomst per poging, en in welke stap
het misging. Er staat nooit een sleutel in de diagnose. Dat blok kun je kopiëren voor support.

Een commando telt alleen als **bevestigd** wanneer een statusuitlezing direct erna (of een latere
uitlezing) de gevraagde stand laat zien. Minimaal 2 seconden tussen commando's per unit. Alleen de
temperatuur of de ventilator wijzigen zet een uitgeschakelde unit niet aan. Bij uitval blijft de
originele afstandsbediening werken.

De waakhond van een zone gebruikt zonder externe sensor de binnentemperatuur van de unit zelf (als die
recent en plausibel is). De bijsturing van het setpoint gebeurt alleen met een externe sensor.

Wat de unit kan, meldt de adapter bij elke uitleesronde (`caps`): modi (uit, auto, koelen,
drogen, ventileren, verwarmen), minimaal 16 en maximaal 30 °C, stapgrootte, en of ventilator en
swing aanwezig zijn.

Uitlezing volgens het protocol:
- `TemSen` = binnentemperatuur, meestal met +40 verschoven (automatisch gecorrigeerd met
  plausibiliteitscontrole, instelbaar via `temsen_offset`). **0 betekent: geen (werkende) sensor**;
  de app toont dan geen temperatuur in plaats van 0 °C.
- `SetTem` is de doeltemperatuur in hele graden Celsius. `TemRec` is het afrondingsbitje voor de
  weergave in Fahrenheit en **niet** "+0,5 °C". Alleen bij een unit waarvan getest is dat hij halve
  graden aanneemt, zet je `half_degrees` aan; dan geldt `TemRec` = +0,5 °C.
- `SwUpDn` 1 en 7–11 zijn swingpatronen. 2–6 zijn **vaste lamelstanden** en tellen niet als swing.
  De stand zelf staat in `louver` (bijvoorbeeld `fixed_middle`).

Bekende onzekerheden: niet alle units rapporteren `TemSen`, het verbruik per binnenunit is niet
meetbaar (gedeelde buitenunit), en of een unit swing of een instelbare ventilator heeft, leiden we af
uit de kolommen die hij teruggeeft.

## HomeWizard

- **Serienummer:** bij elke uitleesronde en gezondheidscontrole vraagt de app `/api` op en vergelijkt
  het serienummer met het gekoppelde apparaat. Antwoordt er een ander HomeWizard-apparaat op het
  adres (bijvoorbeeld na een DHCP-wissel), dan worden er geen metingen opgeslagen en meldt de
  koppeling `identity_mismatch` ("apparaat op ander adres"). Geef HomeWizard-apparaten daarom een
  vast IP-adres (DHCP-reservering in je router). De controle kan minder vaak met
  `identity_check_interval_s`.
- **Energy Socket:** `relay_on` (staat de stekker aan) en `switch_lock` (kan hij niet uit) worden
  elke ronde gelezen via `/api/v1/state`. De app schakelt de boilerstekker nooit. HomeWizard heeft
  voor de stekker nog geen v2-API; mocht een stekker ooit via v2 gekoppeld zijn, dan leest de app de
  stand via v1 (zonder token). Lukt dat niet, dan staat de stand op ‘niet ondersteund’ in plaats van
  een gok.
- **Tijd:** API v2 geeft lokale tijd zonder tijdzone. De adapter rekent die om met de tijdzone
  Europe/Amsterdam (nog niet met de tijdzone van de woning) en kiest de UTC-tijd die het dichtst bij
  het moment van uitlezen ligt (in het dubbele uur eind oktober zijn er twee mogelijkheden; eind maart
  bestaat 02:00–03:00 niet). Wijkt zelfs de beste keuze meer dan een uur af, dan geldt het moment
  van uitlezen. Een gasstand mag wel ouder zijn (uurwaarden), maar niet meer dan een uur in de
  toekomst liggen.

## Philips Hue

- **Knoppen:** bij het inlezen maakt de adapter per knop-apparaat een overzicht met naam, model, de
  knoppen (36 tekens lange id's) en de mogelijke gebeurtenissen per knop. Wat de bridge zelf al met
  een knop doet (`bridge_rules`), komt uit de `behavior_instance`-resources (instellingen uit de
  Hue-app) en, als de bridge ze geeft, uit de oudere v1-regels. Een uitgeschakelde automatisering
  telt niet mee.
- **Gebeurtenissen:** elke knopdruk krijgt een vaste id (`<event-id>:<knop-id>`) zodat dubbele
  verwerking te voorkomen is.
- **Verbinding:** de eventstream heeft een leestime-out van 100 seconden en een waakhond (de bridge
  stuurt geen "ik leef nog"-berichten), TCP-keepalive, en na elke nieuwe verbinding leest de app
  alles opnieuw in. In de status staan "verbonden sinds" en "laatste gebeurtenis".
- **Lichtpagina:** per kamer of zone het aantal lampen aan van het totaal, de actieve scène
  (`scene.status.active`), bereikbaarheid per lamp via het bijbehorende apparaat, en de groep
  "alle lampen" heet "Alle lampen".
- **Commando's:** geeft de bridge in zijn antwoord een foutmelding (bijvoorbeeld "device has
  communication issues"), dan telt het commando als mislukt, met die tekst erbij. Bij een statische
  scène worden de acties per gecontroleerde lamp verstuurd. Een scène telt als bevestigd als de
  bridge de gevraagde lampstanden én dezelfde scène als actief terugmeldt. Als het antwoord een
  resourcetype bevat, moet dat `scene` zijn. Bij een afwijkend type of een inactieve of ontbrekende
  scènestatus blijft de opdracht verzonden (`sent`).
- **Verwijderd op de bridge:** lampen, scènes, kamers en knoppen die in de Hue-app zijn verwijderd
  (ook terwijl de app niet verbonden was), worden na de volgende volledige inleesronde gearchiveerd.
  Hernoemde onderdelen krijgen hun nieuwe naam, tenzij je ze in deze app zelf een naam gaf. Komt een
  onderdeel terug, dan wordt het weer actief.
- **Opnieuw koppelen:** dezelfde bridge opnieuw koppelen (b.v. na een nieuw IP-adres of certificaat)
  werkt de bestaande koppeling bij; oudere dubbele koppelingen van dezelfde bridge gaan uit.

## Zigbee

- De status van Zigbee2MQTT (`bridge/state`, zowel `{"state":"online"}` als het oude `online`) hoort
  bij de bestaande Zigbee-coördinator; er verschijnt geen los apparaat "bridge".
- De gezondheidscontrole meldt de verbinding met de MQTT-broker en de status van Zigbee2MQTT (en dus
  de USB-dongle) apart.
- Een nieuw apparaat krijgt zijn soort (`zigbee_sensor` bij temperatuur of vochtigheid) en zijn
  friendly_name zodra Zigbee2MQTT ze kent (na het interview), ook als de app het apparaat al eerder
  zag.

## Beveiliging van de verbinding (Hue en HomeWizard v2)

De vingerafdruk van het certificaat wordt bij het koppelen vastgelegd en daarna **tijdens de
TLS-handshake** gecontroleerd, voordat er ook maar één byte van het verzoek (met de Hue-sleutel of
het HomeWizard-token) wordt verstuurd. Stuurt het apparaat geen certificaat mee, dan wordt de
verbinding ook geweigerd (`certificate_mismatch`).

## Wat de app zelf regelt en wat niet

- Wifi-instellingen van Gree en HomeWizard: alleen via de fabrikant-app.
- Hue-firmware-updates: via de Hue-app.
- Cloud-accounts zijn na de eerste koppeling niet meer nodig voor de werking van deze app.

## Hoe adapters getest worden

`integrations/tests/contract_kit.py` is een vaste testset die elke adapter moet halen, ook nieuwe:
mogelijkheden zijn opgegeven, gebeurtenissen zijn compleet (tijd met tijdzone, een ontbrekende
waarde is als ontbrekend gemarkeerd), de gezondheid is nooit "ok" zonder koppeling of bij een
onbereikbaar apparaat, een niet-ondersteunde actie geeft een fout (`unsupported`), een onbereikbaar
apparaat geeft een fout en geen resultaat, en een commando is alleen bevestigd als de uitlezing het
laat zien. De tests draaien tegen nep-apparaten die het echte protocol spreken op 127.0.0.1 (UDP voor
Gree, HTTP voor HomeWizard, HTTPS met vastgezet certificaat voor Hue). Dat bewijst het gedrag van de
adapter, niet dat een bepaald fysiek apparaat werkt.
