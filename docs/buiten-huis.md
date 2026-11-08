# Buiten huis: Cloudflare Tunnel

Standaard is de app alleen thuis bereikbaar. Met Cloudflare Tunnel kun je er onderweg bij, zonder poorten open te zetten in je router. De container `cloudflared` maakt een uitgaande verbinding naar Cloudflare.

## Wat je nodig hebt

- Een (gratis) Cloudflare-account met een eigen domein, bijvoorbeeld `jouwdomein.nl`.
- 10 minuten.

## Stappen

1. **Zero Trust › Networks › Tunnels › Create a tunnel** → type *Cloudflared* → naam `huis`.
2. Kopieer het token (de lange tekst na `--token`) naar `/mnt/tank/apps/smart-home/secrets/tunnel_token`.
3. **Public Hostname:** `huis.jouwdomein.nl` → Service `HTTP` → `web:8080`.
4. **Zero Trust › Access › Applications › Add › Self-hosted** voor `huis.jouwdomein.nl`:
   - Policy *Allow* met jouw e-mailadres (eenmalige code per mail).
   - Tweede policy *Service Auth* met een **service token** voor de Android-app (Access › Service credentials). Noteer Client ID en Secret.
5. Maak de YAML opnieuw met `--with tunnel --remote-url https://huis.jouwdomein.nl --tunnel-token-file …` en werk de app bij in TrueNAS (**Apps › smart-home › Edit**).
6. In de Android-app: **Meer › Cloudflare Access** → Client ID en Secret invullen.

## Waarom Cloudflare Access ervoor

De app heeft eigen login, rate limiting en intrekbare telefoon-sleutels. Access voegt een tweede slot toe vóór het verkeer je huis bereikt; aanvallers zien de loginpagina dan niet eens. Zonder Access werkt het ook, maar dan is je loginpagina publiek op internet.

## Beveiliging

- Cookies krijgen automatisch `Secure` zodra `--remote-url` met `https://` begint.
- Elke telefoon heeft een eigen sleutel. Telefoon kwijt: **Systeem › Telefoon koppelen** → intrekken.
- Het token geeft toegang tot je tunnel. Bewaar het alleen in `secrets/` en in de app-YAML.
- Uitzetten: YAML opnieuw maken zonder `tunnel` en de tunnel in Cloudflare verwijderen.
