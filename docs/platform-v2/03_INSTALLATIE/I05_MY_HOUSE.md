# my_house Smart Home Control — installatie, update, test en herstel

**Rol:** Eén eigen klimaat-, verlichting- en energieapp.  
**Aanpak:** Gebruik de standaard Custom App. Controleer de inbedrijfstelling per apparaat.
**Bron:** https://github.com/lvos04/my_house.  
Gebruik voor versie 0.3.0 de vaste alles-in-één-image `ghcr.io/lvos04/my_house:0.3.0@sha256:cf3ad1227e57cf70a5f21577e71a79b64c77f6446652ac1bf6bff27e994e1047`. [Formuliervelden](../../../deploy/truenas/CUSTOM_APP.md)

## 1. Vooraf controleren
TrueNAS versie; Hue bridge, Gree modellen, HomeWizard lokale API, Zigbee dongle indien gekocht.

**Toegang / netwerk:** Kies een vrije hostpoort voor de portal op het vertrouwde netwerk.

**Data / mounts:** Kies voor de eerste installatie een lege dataset en koppel die aan `/appdata`. De image bevat PostgreSQL, MQTT, API, worker, web en optioneel Zigbee2MQTT.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Dataset en image
**Uitvoeren:** Maak in TrueNAS een eigen lege dataset. Bewaar bij een overstap de oorspronkelijke opslag met haar snapshot en sleutels. Controleer het releasebewijs van de gekozen image.
**Verifiëren:** De dataset en de vaste image zijn herkenbaar. Alleen de bedoelde diensten en de beheerder hebben toegang tot de benodigde gegevens.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Formulier standaard Custom App zonder Zigbee/tunnel
**Uitvoeren:** Open **Apps › Discover Apps › Custom App**. Vul de image, portal, poort en opslag in volgens de [formulierhandleiding](../../../deploy/truenas/CUSTOM_APP.md).
**Verifiëren:** De dataset is aan `/appdata` gekoppeld. Alleen de gekozen webpoort is gepubliceerd.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. TrueNAS Install via formulier standaard Custom App
**Uitvoeren:** Vraag de startmodule op via het formulier en lees de naam in het logboek. Leg de module vast bij de gekozen tag en digest en sla het formulier met die module op.
**Verifiëren:** De digest komt overeen met het releasebewijs, de healthcheck is groen en aanmelden via de portal lukt.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Hue koppeling
**Uitvoeren:** Hue bridge lokaal gekoppeld na druk op knop, lees scenes/knoppen, één testlicht zonder dubbele groep.
**Verifiëren:** Schakelaar + readback klopt.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. HomeWizard rollen
**Uitvoeren:** Enable Local API; P1 → huis totaal, Energy Socket → boiler read-only, 1-fase meter → airco totaal.
**Verifiëren:** Energie cumulatieve tellers en kwaliteit zichtbaar; boiler heeft geen write.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Gree compatibiliteit
**Uitvoeren:** Koppel Gree+ 2.4G, ontdek/bind/read, als unsupported geen actuation; test conflict gedeelde buitenunit.
**Verifiëren:** Bij uitval valt regelaar terug, remote OFF blijft OFF.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 7. Zigbee optioneel
**Uitvoeren:** Selecteer de stick in **Devices** en kies de bijbehorende adapter volgens de [formulierhandleiding](../../../deploy/truenas/CUSTOM_APP.md#zigbee-stick-alleen-als-je-er-een-hebt).
**Verifiëren:** MQTT connect en temperatuursensor met battery/stale alarms.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-07`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 8. Android en restore
**Uitvoeren:** Koppel de telefoon via **Account › Telefoons** en toets intrekken. Maak in de webapp een volledige versleutelde export en volg op een afzonderlijke Custom App de [herstelwizard](../../onderhoud.md#herstellen).
**Verifiëren:** Telefoon en web werken na reboot; verhuizing zonder dataverlies.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-08`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel automatisering en ingest uit. Bewaar audit en logs en stop de nieuwe app.
2. Zet de passende database of snapshot terug op aparte opslag. Volg [Offline terugvalkopie voorbereiden](../../onderhoud.md#offline-terugvalkopie-voorbereiden). Kies het eenmalige startprogramma van de huidige image in het formulier en controleer de eindmelding en exitcode 0 voor die kopie. Bij een fout blijft de oude worker uit.
3. Start de bijpassende vaste image via het alleen-lezen startbestand met `terugval-app` en het Terugval-ID van de kopie. Controleer aanmelding, clients en gegevensintegriteit.
4. Leg in het incidentrapport vast welke gegevens zijn teruggezet. Voor het verwijderen van gegevens bij uitfasering is afzonderlijke bevestiging nodig.

## 5. Let op
Niet claimen dat my_house al met echte apparaten getest is. Boiler-schakelverbod zowel in API als via Hub/AI afdwingen.

## 6. Upstream documentatie en broncontrole
https://github.com/lvos04/my_house

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.
