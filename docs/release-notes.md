Eerste versie van Smart Home Control: één lokale app voor Hue, Gree-airco, HomeWizard en Zigbee, als TrueNAS Custom App, met web-app en Android-app.

**Let op:** nog niet getest met echte apparaten. Elke adapter is getest tegen de officiële documentatie; in de app staan apparaten op "nog niet getest" tot ze werken. Zie `docs/testlog.md`.

## Installeren

- **Server (TrueNAS SCALE 25.10 of Linux):** volg `docs/installatie-truenas.md`. Images:
  - `ghcr.io/lvos04/my_house-api:0.1.0`
  - `ghcr.io/lvos04/my_house-web:0.1.0`
- **Android:** download `huis-v0.1.0.apk` hieronder en installeer (toestaan: *installeren uit onbekende bron*). Koppel via **Systeem › Telefoon koppelen** in de web-app.

## Inhoud

- Klimaatregeling voor twee Gree-units met schema's, handmatige bediening, veilige terugval bij verouderde sensor en bescherming van de gedeelde buitenunit.
- Energie per dag, week, maand en jaar uit HomeWizard-tellers, met kosten en export.
- Boiler-stekker alleen meten; schakelen geblokkeerd.
- Hue-groepen, scènes en knoppen.
- Zigbee-sensoren via Zigbee2MQTT met koppelvenster.
- Opt-in zelflerende bijsturing (max ±0,5°) en experimenten met eerlijke vergelijking.
- Versleutelde back-ups, herstel in veilige modus, verhuisassistent.
- Optioneel bereikbaar buiten huis via Cloudflare Tunnel.

De APK is ondertekend met de eigen release-sleutel uit de GitHub-geheimen, geschikt om zelf te installeren (niet via de Play Store). Zonder ingestelde sleutel bevat de release geen APK; zie `docs/installatie-truenas.md`.
