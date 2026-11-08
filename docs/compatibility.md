# Compatibiliteit en ondersteunde apparaten

**Status van alle regels hieronder: nog niet getest met echte hardware.**
De adapters zijn gebouwd volgens de openbare documentatie van de fabrikanten en getest met
contracttests op basis van die documentatie (zie `integrations/tests/fixtures/README.md`).
Een regel gaat pas naar "getest" nadat hij met een echt apparaat in dit huis is gecontroleerd;
noteer dan datum, firmware en wie het testte.

| Fabrikant | Model / producttype | API / protocol | Lokaal in de app | Eerst nodig in fabrikant-app | Status |
|---|---|---|---|---|---|
| Philips Hue | Bridge v2 (BSB002) | CLIP API v2 (HTTPS, eventstream) | Lampen, groepen, scènes, knop- en draai-events | Lampen/knoppen toevoegen aan de bridge (Hue-app) | nog niet getest met echte hardware |
| Philips Hue | Bridge v1 (rond) | — | Niet ondersteund (geen API v2) | — | niet ondersteund |
| HomeWizard | P1-meter (HWE-P1) | v1 (HTTP) en v2 (HTTPS + token) | Vermogen, kWh import/export per tarief, gas | ‘Lokale API’ aan in Energy-app; v2: knop op meter indrukken bij koppelen | nog niet getest met echte hardware |
| HomeWizard | Energy Socket (HWE-SKT) | v1 (HTTP) | Vermogen en kWh. Schakelen alleen als het beleid het toestaat; **boiler-stekker nooit** | ‘Lokale API’ aan in Energy-app | nog niet getest met echte hardware |
| HomeWizard | kWh-meter 1-fase (HWE-KWH1) | v1 en v2 | Vermogen, kWh import/export | ‘Lokale API’ aan; v2: knop indrukken | nog niet getest met echte hardware (nog te bestellen) |
| HomeWizard | kWh-meter 3-fase (HWE-KWH3), SDM230-wifi, SDM630-wifi | v1 (SDM) / v1+v2 (KWH3) | Vermogen, kWh | ‘Lokale API’ aan | nog niet getest met echte hardware |
| Gree | Multisplit binnenunits (typenummer onbekend) | Lokaal UDP-protocol poort 7000, AES-ECB (v1) of AES-GCM (v2) | Aan/uit, modus, doeltemperatuur (stap 0,5 °C indien TemRec), ventilator, swing, binnentemperatuur (TemSen) | Wifi-koppeling via Gree+-app op 2,4 GHz | nog niet getest met echte hardware — **compatibiliteitsgate**: binden + status lezen moet slagen, anders ‘niet ondersteund’ |
| Zigbee | Aqara WSDCGQ11LM (temp/vocht/druk) | Zigbee2MQTT via MQTT | Temperatuur, vochtigheid, druk, batterij, linkkwaliteit | Geen | nog niet getest met echte hardware |
| Zigbee | Coördinator: SONOFF ZBDongle-E (EFR32MG21, driver `ember`) of ZBDongle-P (CC2652P, driver `zstack`) | Zigbee2MQTT | — | Firmware volgens Zigbee2MQTT-adapterlijst | nog niet gekocht / nog niet getest |

## Gree: wat de gate controleert

1. De unit antwoordt op een `scan` op UDP 7000 (MAC, firmware `ver`, `mid` worden als diagnose bewaard).
2. Binden met de generieke sleutel (v1 of v2, automatisch herkend) levert een eigen apparaatsleutel op.
3. Een statusverzoek met die sleutel geeft geldige waarden terug.

Pas als alle drie slagen wordt de unit gekoppeld. Een commando telt alleen als **bevestigd**
wanneer een statusuitlezing direct erna de gevraagde stand laat zien. Minimaal 2 seconden
tussen commando's per unit. Bij uitval blijft de originele afstandsbediening werken.

Bekende onzekerheden: sommige firmwares rapporteren `TemSen` met +40 offset (automatisch
gecorrigeerd met plausibiliteitscontrole, instelbaar via `temsen_offset`), niet alle units
rapporteren `TemSen`, en het verbruik per binnenunit is niet meetbaar (gedeelde buitenunit).

## Wat de app zelf regelt en wat niet

- Wifi-instellingen van Gree en HomeWizard: alleen via de fabrikant-app.
- Hue-firmware-updates: via de Hue-app.
- Cloud-accounts zijn na de eerste koppeling niet meer nodig voor de werking van deze app.
