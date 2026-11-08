# Problemen oplossen

| Wat je ziet | Oorzaak | Oplossing |
|---|---|---|
| App start niet, log: `SHC_SECRET_KEY … ontbreekt` | `install.sh` niet uitgevoerd | Voer `install.sh <root>` uit en maak de YAML opnieuw |
| **Systeem**: *Achtergronddienst — geen hartslag* | Worker-container draait niet | TrueNAS › Apps › smart-home › logs van `worker` |
| Hue: *link-knop niet ingedrukt* | Normaal bij koppelen | Druk op de knop op de bridge en klik binnen 30 s op Koppelen |
| Hue: *certificaat gewijzigd* | Bridge vervangen of gereset | Verwijder de integratie en koppel opnieuw |
| HomeWizard: *Lokale API uit* | Instelling in de Energy-app | Energy-app › apparaat › Meter-instellingen › Lokale API aan |
| Gree: *niet ondersteund* of geen antwoord | Model/firmware zonder lokaal protocol, of unit op 5 GHz/ander VLAN | Controleer 2,4 GHz-wifi via Gree+; zie de diagnose bij het apparaat. Gebruik de afstandsbediening; de rest van de app werkt door |
| Zoeken vindt niets | Containers zien geen broadcast/mDNS | Vul het IP-adres in; geef apparaten een vast IP in de router |
| Zigbee2MQTT start niet | Dongle niet gevonden of verkeerd pad | `ls -l /dev/serial/by-id/` en gebruik dat pad; controleer `adapter` (ember/zstack) in `configuration.yaml` |
| Sensor *verouderd* | Batterij leeg of bereik | Batterij vervangen; dongle met verlengkabel weg van USB 3; een netgevoed Zigbee-apparaat als repeater |
| Energie: dag met streepjesrand | Ontbrekende metingen (meter offline) | Normaal; totalen gebruiken de tellerstanden en worden zodra metingen terugkomen weer juist |
| *Veilige modus* | Na herstel/verhuizing | Controleer **Apparaten** en klik in **Systeem** op *Apparaten kloppen* |
| Android: *Onderweg* werkt niet | Tunnel/Access | Controleer de Access service token in **Meer** en de public hostname naar `web:8080` |
| Android: QR-code ongeldig | Code verlopen (10 min) of al gebruikt | Maak een nieuwe code |
