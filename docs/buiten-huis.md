# Buiten huis: Cloudflare Tunnel

Standaard is de app alleen thuis bereikbaar. Met Cloudflare Tunnel kun je er onderweg bij, zonder poorten open te zetten in je router. De container `cloudflared` maakt een uitgaande verbinding naar Cloudflare.

## Wat je nodig hebt

- Een (gratis) Cloudflare-account met een eigen domein, bijvoorbeeld `jouwdomein.nl`.
- Ongeveer 15 minuten.

## Stappen

1. **Zero Trust › Networks › Tunnels › Create a tunnel** → type *Cloudflared* → naam `huis`.
2. Kopieer het token (de lange tekst na `--token`) naar het bestand `secrets/tunnel_token` in je dataset, en voer daarna `install.sh` opnieuw uit. Dat geeft het bestand de juiste eigenaar (alleen `cloudflared` mag het lezen):

   ```bash
   sudo nano /mnt/tank/apps/smart-home/secrets/tunnel_token
   sudo /mnt/tank/apps/smart-home/src/infra/scripts/install.sh /mnt/tank/apps/smart-home
   ```

3. **Public Hostname:** `huis.jouwdomein.nl` → Service `HTTP` → `web:8080`. Iets anders dan `web:8080` werkt niet: de tunnel kan alleen de webserver van de app bereiken, niet de database, de API of de MQTT-broker.
4. **Zero Trust › Access › Applications › Add › Self-hosted** voor `huis.jouwdomein.nl`:
   - Policy *Allow* met jouw e-mailadres (eenmalige code per mail).
   - Tweede policy *Service Auth* met een **service token** voor de Android-app (**Access › Service credentials**). Noteer Client ID en Secret.
   - Kies bij **Session Duration** hoe lang je ingelogd blijft (zie [Cloudflare Access-sessie](#cloudflare-access-sessie)).
5. **Altijd https.** Open in het gewone Cloudflare-dashboard je domein › **SSL/TLS › Edge Certificates**:
   - Zet **Always Use HTTPS** aan. Wie per ongeluk `http://huis.jouwdomein.nl` intypt, gaat dan meteen naar https.
   - Zet **HTTP Strict Transport Security (HSTS)** aan: *Max Age* 6 maanden, *Apply HSTS policy to subdomains* **uit** (tenzij al je subdomeinen https hebben), *No-Sniff* aan, *Preload* uit. Je browser gebruikt daarna voor dit adres nooit meer gewone http. De app stuurt via de tunnel zelf ook een HSTS-header mee.
6. Maak de YAML opnieuw met `--with tunnel --remote-url https://huis.jouwdomein.nl` en werk de app bij in TrueNAS (**Apps › smart-home › Edit**). Het token komt níet in de YAML: de app leest het uit `secrets/tunnel_token`.
7. In de Android-app: **Meer › Cloudflare Access** → Client ID en Secret invullen.

Werkt de tunnel niet? Kijk in TrueNAS bij **Apps › smart-home** naar de logs van `cloudflared`:

| Melding | Oplossing |
|---|---|
| `Failed to read token file … permission denied` | Voer `install.sh` opnieuw uit. |
| `Provided Tunnel token is not valid` | Het token is niet volledig gekopieerd. Kopieer het opnieuw (één regel, zonder spaties). |
| `tunneltoken niet gevonden` (bij het maken van de YAML) | Zet eerst het token in `secrets/tunnel_token`. |

## Thuis http, onderweg https

Je hebt nu twee adressen:

- **Thuis:** `http://<NAS-IP>:8088`. Gewone http, alleen op je eigen netwerk.
- **Onderweg:** `https://huis.jouwdomein.nl`. Versleuteld via Cloudflare.

Beide werken naast elkaar. De app bepaalt per verzoek hoe het binnenkwam:

- Via de tunnel over https krijgt de inlogcookie het kenmerk *Secure*. De browser stuurt hem dan nooit onversleuteld mee.
- Thuis via http krijgt de cookie dat kenmerk niet, anders zou de browser hem weigeren en lukt inloggen niet.

De app gelooft alleen de tunnel als die zegt "dit kwam via https binnen". Een ander apparaat op je netwerk kan dat niet nadoen. Om dezelfde reden ziet de app bij inlogpogingen via de tunnel het echte IP-adres van de bezoeker, en werkt de limiet op inlogpogingen per bezoeker (maximaal 10 per minuut).

Voor de browser zijn het twee aparte sites: thuis en onderweg log je elk één keer in. HSTS geldt alleen voor `huis.jouwdomein.nl`, niet voor het IP-adres van de NAS.

## Eén https-adres, thuis én onderweg (optioneel)

Wil je de web-app als app op je telefoon installeren (PWA), dan is https nodig. Thuis via `http://<NAS-IP>:8088` kan dat niet. Je hebt twee mogelijkheden:

**A. Gebruik thuis ook het tunneladres.** Installeer de web-app vanaf `https://huis.jouwdomein.nl`. Dat werkt meteen, maar thuis gaat dan al het verkeer via internet en Cloudflare. Valt je internet uit, dan doet de web-app het thuis niet meer (de Android-app wel: die kiest zelf het thuisadres).

**B. Split-horizon DNS: thuis wijst hetzelfde adres naar de NAS.** Je DNS thuis (router, Pi-hole of AdGuard Home) geeft voor `huis.jouwdomein.nl` het IP-adres van een eigen reverse proxy op je netwerk, in plaats van Cloudflare. Onderweg blijft alles via de tunnel gaan. Je hebt dan nodig:

- Een reverse proxy thuis (bijvoorbeeld Caddy, Traefik of Nginx Proxy Manager) met een geldig certificaat voor `huis.jouwdomein.nl`. Gebruik daarvoor Let's Encrypt met de **DNS-challenge** via Cloudflare, met een API-token dat alleen DNS mag wijzigen in jouw zone. Je hoeft dan geen poort open te zetten.
- De proxy stuurt door naar `http://<NAS-IP>:8088` en:
  - laat de `Host`-header ongewijzigd (`huis.jouwdomein.nl`, zonder poort);
  - ondersteunt WebSocket voor `/api/v1/ws` (live-updates);
  - laat uploads tot 2 GB toe voor `/api/v1/system/restore` (een back-up terugzetten).

Goed om te weten bij B:

- Thuis loopt het verkeer niet via Cloudflare, dus Cloudflare Access doet thuis niets. De eigen login van de app beschermt de app dan, net als op `http://<NAS-IP>:8088`.
- De app vertrouwt alleen de tunnel als bron van "dit was https". Achter je eigen proxy krijgt de inlogcookie thuis dus geen *Secure*-kenmerk. Op je eigen netwerk is dat aanvaardbaar: je browser gebruikt voor dit adres door HSTS toch altijd https.
- Telefoons met *Privé-DNS* (DNS-over-HTTPS) slaan de DNS van je router over en gaan dan toch via de tunnel. Dat werkt ook, alleen via internet.
- Laat in de Android-app het thuisadres op `http://<NAS-IP>:8088` staan.

## Cloudflare Access-sessie

Na het inloggen bij Cloudflare Access (de mail met de code) blijf je een tijd ingelogd. Standaard is dat **24 uur**. Je stelt dit in bij **Zero Trust › Access › Applications › huis › Session Duration**, van 15 minuten tot 1 maand. Een week is een redelijke keuze voor een persoonlijke app.

**Wat gebeurt er als de sessie verloopt?**

- **Browser:** de volgende keer dat je de pagina opent of herlaadt, zie je de inlogpagina van Cloudflare. Vraag een nieuwe code aan en log in.
- **Web-app die al open stond of als app is geïnstalleerd (PWA):** de app krijgt dan de Cloudflare-inlogpagina terug in plaats van gegevens, en kan "Server niet bereikbaar" of "Geen verbinding" tonen terwijl je internet gewoon werkt. Oplossing: herlaad de pagina (trek de pagina omlaag, of druk op F5). Helpt dat niet, sluit de app dan helemaal, open `https://huis.jouwdomein.nl` één keer in de browser, log in bij Cloudflare en open de app opnieuw.
- **Android-app:** die gebruikt het *service token* en heeft geen sessie die na een dag verloopt. Het service token zelf verloopt wel. De standaardduur is één jaar; kies bij het aanmaken de duur en zet een herinnering. Vernieuwen: **Access › Service credentials › huis › Refresh**, en vul het nieuwe Secret in bij **Meer › Cloudflare Access**.

De Cloudflare-sessie staat los van je inlog in de app zelf: die blijft 14 dagen geldig.

## Waarom Cloudflare Access ervoor

De app heeft eigen login, rate limiting en intrekbare telefoon-sleutels. Access voegt een tweede slot toe vóór het verkeer je huis bereikt; aanvallers zien de loginpagina dan niet eens. Zonder Access werkt het ook, maar dan is je loginpagina publiek op internet.

## Beveiliging

- De inlogcookie krijgt automatisch *Secure* als je via de tunnel over https binnenkomt (zie hierboven). Zet `SHC_COOKIE_SECURE` niet zelf op `true`: dan lukt inloggen thuis via http niet meer.
- Het tunneltoken staat alleen in `secrets/tunnel_token` (rechten 0400, alleen leesbaar voor de `cloudflared`-container). Het staat niet in de YAML van de app en niet in back-ups. Denk je dat het is uitgelekt? Maak in Cloudflare een nieuw token voor de tunnel (of een nieuwe tunnel), zet het in `secrets/tunnel_token`, voer `install.sh` opnieuw uit en herstart de app.
- `cloudflared` zit in een eigen netwerk, samen met alleen de webserver. Ook een verkeerde instelling in het Cloudflare-dashboard kan de database of de MQTT-broker dus niet bereikbaar maken.
- Elke telefoon heeft een eigen sleutel, die na 180 dagen verloopt als de app hem niet vernieuwt. Telefoon kwijt: trek hem in via **Account › Telefoons** in de web-app. Ook met een gestolen telefoon met rol *Beheren* kan iemand geen gebruikers, koppelcodes of back-ups maken en geen adressen wijzigen: dat kan alleen in de web-app na inloggen met wachtwoord.
- De Android-app stuurt zijn sleutel naar het thuisadres alleen nadat de server een controlevraag goed heeft beantwoord (zie [apps/android/README.md](../apps/android/README.md#herkent-de-app-je-server)). Via de tunnel bewijst het https-certificaat al wie er antwoordt.
- Houd de app bijgewerkt: elke versie bevat een recente `cloudflared`. Cloudflare ondersteunt versies tot ongeveer een jaar oud; een oudere versie kan zomaar stoppen met verbinden.
- Uitzetten: YAML opnieuw maken zonder `tunnel`, de tunnel in Cloudflare verwijderen en `secrets/tunnel_token` wissen.
