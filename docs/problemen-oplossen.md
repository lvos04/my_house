# Problemen oplossen

Open in TrueNAS **Apps** en kies de betreffende standaard Custom App. Gebruik het logboek voor een startfout en **Edit** om de gekozen image, opslag, poort of apparaten te controleren. [Formuliervelden](../deploy/truenas/CUSTOM_APP.md) · [Onderhoud en herstel](onderhoud.md)

## Installatie en starten

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| De app meldt een ontbrekende sleutel of onbekende opslag | De geselecteerde dataset is onvolledig of hoort bij een andere installatie | Stop de app. Controleer in het formulier welke dataset aan `/appdata` is gekoppeld. Bewaar de gebruikte opslag en de snapshot met de oorspronkelijke sleutels. Volg bij herstel de [terugvalprocedure op een kloon](onderhoud.md#offline-terugvalkopie-voorbereiden). |
| De eerste start meldt een rechtenfout | De app kan haar eigen opslag niet voorbereiden | Controleer de gekozen lege dataset, **Read Only**, **Custom User** en de toegangsrechten volgens de [formulierhandleiding](../deploy/truenas/CUSTOM_APP.md#beveiliging-netwerk-en-opslag). |
| TrueNAS meldt een bezette hostpoort | De gekozen poort is al in gebruik | Kies bij **Ports** een vrije hostpoort en gebruik diezelfde poort bij **Portals**. |
| *De app start of wordt bijgewerkt…* blijft staan | De API is nog niet beschikbaar | Open het opstartlog van de Custom App. Bij *Database-update geweigerd* controleer je of er ruimte voor de pre-migratiedump is. Bij een nieuwere schemarevisie kies je de bijpassende versie in het formulier. |
| Ik kom na het beheerdersaccount niet verder | Nog geen woning | Vul het formulier *Woning aanmaken* in; daarna helpt de installatiewizard (`/installatie`) |

## Achtergronddienst, back-ups en meldingen

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| Melding *Achtergronddienst reageert niet* | De hartslag van de worker is te oud | Bekijk in **Systeem** de laatste hartslag en open in TrueNAS het logboek van de app. De bewaker meldt wanneer een kerndienst opnieuw start. |
| *Achtergronddienst*: *Onderhoudsmodus* | De worker is gepauzeerd | Bekijk de taak bij **Systeem › Herstellen en verhuizen**. Bij **Onbekende uitkomst** meld je je opnieuw aan en controleer je de doelgegevens en het taakrapport vóór je het herstel hervat. |
| Melding *Automatische back-up mislukt* | Zie de tekst bij de melding (b.v. schijf vol) | De worker probeert het opnieuw na 1, 2, … tot 6 uur. Los de oorzaak op; de melding verdwijnt na de volgende geslaagde back-up |
| Melding *Automatische back-up kan niet* | De opgeslagen wachtwoordzin past niet bij de huidige hoofdsleutel | Lees na herstel het bijbehorende sleutelbestand in of stel de wachtwoordzin opnieuw in bij **Systeem › Back-ups en bewaren**. [Hoofdsleutel vervangen](onderhoud.md#hoofdsleutel-vervangen) |
| *Back-up*: *Zigbee in laatste back-up: nee* | De volledige export bevat geen Zigbee-bestanden | Gebruik je Zigbee, controleer dan de dongle en de Zigbee-status. Bewaar de oorspronkelijke dataset en maak een volledige back-up. Voor bestaande Zigbee-bestanden ontbreekt een beproefd webherstel. |
| **Systeem › Meldingen buiten de app**: *Opgeslagen push-adres onbruikbaar* | Het adres is met een andere hoofdsleutel versleuteld | Vul het adres opnieuw in, of kies *Adres verwijderen* |
| Testbericht komt niet aan | Adres fout of ntfy niet bereikbaar | Controleer het onderwerp in de ntfy-app; de foutmelding staat bij *Testbericht sturen* |
| *Tijdsynchronisatie* blijft *onbekend* of waarschuwt | De tijdserver is onbereikbaar of de klok wijkt af | Controleer de tijdinstellingen en de bereikbaarheid van de tijdserver. Een eigen tijdserver stel je in via **Environment Variables › SHC_NTP_SERVER** in het formulier. |
| *Vrije schijfruimte* oranje | Minder dan 15 % vrij | Controleer de beschikbare ruimte en de bewaartermijnen. Bewaar de geverifieerde back-up en de snapshot van vóór de update voor terugval. |

## Apparaten

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| Hue: *link-knop niet ingedrukt* | Normaal bij koppelen | Druk op de knop op de bridge en klik binnen 30 s op Koppelen |
| Hue: *certificaat gewijzigd* (`certificate_mismatch`) | Bridge vervangen of gereset | Koppel de bridge opnieuw; de bestaande koppeling wordt bijgewerkt |
| *Sleutel past niet, koppel opnieuw* | De apparaatsleutel is met een andere hoofdsleutel versleuteld | Open **Systeem › Herstellen en verhuizen › Apparaatsleutels herstellen**. Kies het bewaarde bronbestand of koppel het betreffende apparaat opnieuw. [Hoofdsleutel vervangen](onderhoud.md#hoofdsleutel-vervangen) |
| HomeWizard: *Lokale API uit* | Instelling in de Energy-app | Energy-app › apparaat › Meter-instellingen › Lokale API aan |
| HomeWizard: *apparaat op ander adres* (`identity_mismatch`) | Een ander HomeWizard-apparaat antwoordt op het oude IP (DHCP-wissel) | Pas het adres aan bij het apparaat op **Apparaten** (de app controleert het serienummer) en geef het een vast IP |
| Gree: *niet ondersteund* of geen antwoord | Model/firmware zonder lokaal protocol, of unit op 5 GHz/ander VLAN | Controleer 2,4 GHz-wifi via Gree+; zie de diagnose bij het apparaat (kopieerbaar). Gebruik de afstandsbediening; de rest van de app werkt door |
| Zoeken vindt niets | De app ontvangt geen broadcast of mDNS van het apparaat | Vul het adres van het apparaat in. Geef het apparaat zo nodig een vast adres in de router. |
| Koppelen: *Dit adres hoort bij de app zelf* of *niet toegestaan* | Adres in de interne netwerken, loopback of buiten het thuisnetwerk | Gebruik het IP-adres van het apparaat in je thuisnetwerk; uitzonderingen in `SHC_PAIRING_ALLOWED_HOSTS` |
| Zigbee2MQTT start niet | De dongle of adapterkeuze ontbreekt | Controleer **Devices**, **Container Device** en het adaptertype in het Custom App-formulier. [Zigbee-stick](../deploy/truenas/CUSTOM_APP.md#zigbee-stick-alleen-als-je-er-een-hebt) |
| *Zigbee – USB-dongle* rood, *MQTT-verbinding* groen | Zigbee2MQTT draait niet (dongle los) | Steek de dongle terug; Zigbee2MQTT herstart en de status wordt vanzelf groen |
| Sensor *verouderd* | Batterij leeg of bereik | Batterij vervangen; dongle met verlengkabel weg van USB 3; een netgevoed Zigbee-apparaat als repeater |
| Waakhond: *<zone>: temperatuur reageert niet* | De kamer komt niet dichter bij het doel (raam open, airco in verkeerde modus of defect) | Controleer de unit en de kamer. De melding verdwijnt als de temperatuur weer reageert; zet de waakhond bij de zone eventueel langer of uit |
| Commando blijft *Verzonden · wacht op bevestiging* en wordt *Mislukt* | De unit liet de gevraagde stand niet zien | Kijk bij de zone in het logboek; probeer het opnieuw of gebruik de afstandsbediening |
| *Kies eerst een modus* | De airco staat uit | Kies een modus (verwarmen, koelen …); alleen temperatuur wijzigen zet een uitgeschakelde unit niet aan |
| *Veilige modus* | Na herstel/verhuizing | Controleer **Apparaten** en kies in **Systeem** *Apparaten kloppen — veilige modus opheffen* |

## Energie

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| Dag of uur met *geschat* / *ontbrekende metingen* | Meter (tijdelijk) offline | Normaal; totalen komen uit de tellerstanden en worden juist zodra er weer metingen zijn |
| *verdachte meterstand overgeslagen* | Een sprong groter dan het maximale vermogen van die rol | Klopt het maximum niet (b.v. zwaardere aansluiting)? Pas het aan bij **Energie › Meetgrenzen** |
| *Overig* is *geschat* | Een tussenmeter (airco of boiler) mist metingen | Controleer die meter op **Apparaten** |
| Kosten *gedeeltelijk* | Niet de hele periode valt onder een tarief | Voeg een tarief toe met een eerdere ingangsdatum |

## Inloggen en telefoons

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| *Te veel mislukte pogingen* | 5 keer een fout wachtwoord voor deze naam, of te veel pogingen vanaf dit adres | Wacht (1 minuut, oplopend tot 15). Een andere beheerder kan je ontgrendelen bij **Account › Gebruikers** |
| Wachtwoord vergeten | De inloggegevens ontbreken | Een andere ingelogde beheerder kan je wachtwoord opnieuw instellen onder **Account › Gebruikers**. Zonder toegankelijk beheerdersaccount is er geen zelfherstel via de webapp. [Wachtwoord vergeten](onderhoud.md#wachtwoord-vergeten) |
| Telefoon: *Dit kan alleen in de web-app* | Gebruikers, telefoons, back-ups, herstellen, adressen en push-adres zijn alleen voor de web-app met wachtwoord | Doe het in de web-app |
| Android: QR-code ongeldig | Code verlopen (10 min) of al gebruikt | Maak een nieuwe code bij **Account › Telefoons** |
| Android: *Dit is niet jouw server* | Op dit adres antwoordt een ander apparaat, of de server is ouder dan de app | Controleer het wifi-netwerk. Werk eerst de server bij en daarna de app; via de tunnel blijft de app werken |
| Android: *Deze telefoon is niet meer gekoppeld* | Ingetrokken, verlopen (180 dagen) of onbekend na een herstel | Koppel opnieuw met een QR-code via **Account › Telefoons** |
| Android: *Koppel deze telefoon opnieuw, zodat de app je server kan herkennen…* | Gekoppeld met een oudere app en geen tunnel ingesteld | Koppel opnieuw |
| Android: *Onderweg* werkt niet | De alles-in-één-image bevat geen actieve tunnel | Gebruik voor de Custom App het vertrouwde netwerk. De [tunnelhandleiding](buiten-huis.md) hoort bij de ontwikkelomgeving. |
| Web-app via de tunnel toont *Geen verbinding* terwijl internet werkt | De Cloudflare Access-sessie is verlopen | Herlaad de pagina en log opnieuw in bij Cloudflare (zie [buiten-huis.md](buiten-huis.md#cloudflare-access-sessie)) |

## Herstellen

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| *Deze back-up is gemaakt met een nieuwere versie* | De back-up komt van een nieuwere app | Werk de app eerst bij naar die versie of nieuwer |
| *Onbekende uitkomst* tijdens terugzetten | De herstelpagina heeft de eindstand van de taak niet kunnen bevestigen | Meld je aan op de doelapp en controleer de taak en de herkomst van de doelgegevens. Bewaar de geverifieerde doelback-up. |
| De herstelproef meldt onleesbare apparaatsleutels | De huidige hoofdsleutel verschilt van die van de bron | Bewaar de bron en de oorspronkelijke sleutel. Lees na herstel het bestand in bij **Oude hoofdsleutel uploaden** en controleer de aantallen. [Herstellen](onderhoud.md#herstellen) |
| *Er loopt nog een back-up* | Herstel tijdens een back-up | Wacht tot de back-up klaar is |
