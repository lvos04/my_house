#!/bin/sh
# Instellingen voor de web-app, gemaakt bij de start van de container (nginx-image: /docker-entrypoint.d/).
# Alleen "Terug naar de Hub" (O-11, ADR-0008 punt 3, contract 1.3 §2.3 regel 5): de optionele omgevingsvariabele
# HUB_URL wordt na controle geschreven als {"hub_url": "<origin>"}; leeg, afwezig of ongeldig = {} (de app toont dan
# geen link). HUB_URL start in my_house nooit een koppeling (geen enrollment, geen token, geen vingerafdruk).
#
# Regels van contract 1.3 §3.2 (adresregels, vorm):
# - alleen http:// of https:// + host + optionele poort (1-65535, zonder voorloopnul), hooguit één afsluitende "/";
#   geen pad, query, fragment of inloggegevens; alleen ASCII-letters, cijfers en . - : [ ];
# - IPv4 alleen als a.b.c.d (vier decimale delen 0-255, zonder voorloopnul). Een host waarvan het laatste deel een
#   getal is (ook 0x…), leest de browser als IPv4-adres (127.1, 2130706433, 0x7f000001, 010.0.0.1, 0): ongeldig als
#   hij niet precies die vorm heeft;
# - IPv6 alleen tussen [], zonder zone; strenger dan het contract: ook zonder IPv4-notatie ([::ffff:1.2.3.4]),
#   omdat die hier niet betrouwbaar te herleiden is (geen Hub heeft zo'n adres nodig);
# - één punt aan het eind van een naam telt niet mee (localhost. = localhost);
# - verboden: loopback (127.0.0.0/8, ::1, localhost, *.localhost), "dit netwerk"/geen adres (0.0.0.0/8, ::),
#   link-local (169.254.0.0/16, fe80::/10) en IPv4-gemapte adressen (::ffff:0:0/96). Er is geen ontwikkelvlag voor
#   loopback: my_house heeft die niet nodig.
# my_house maakt zelf nooit verbinding met dit adres (het is alleen het doel van een link in de browser van de
# gebruiker); de regel "verboden bereiken ook na DNS-resolutie bij elke verbinding" heeft hier dus geen verbinding om
# te bewaken (ADR-0012). Een ongeldige waarde stopt de container nooit.
# nginx serveert het bestand als /_shc/config.json (nginx.conf.template). SHC_CONFIG_DIR alleen voor tests.
set -eu

dir="${SHC_CONFIG_DIR:-/tmp/shc-config}"
json='{}'

if [ -n "${HUB_URL:-}" ]; then
  # De waarde gaat via ENVIRON naar awk (niet via -v: dat zou backslashes interpreteren). POSIX-awk zonder
  # intervallen ({n,m}), zodat busybox-awk (image), mawk en gawk hetzelfde doen.
  uitkomst="$(HUB_URL="$HUB_URL" awk '
    function fout(reden) { print "fout " reden; exit 0 }
    function vier(g) { while (length(g) < 4) g = "0" g; return g }
    function groep(g) { return g ~ "^[0-9a-f][0-9a-f]?[0-9a-f]?[0-9a-f]?$" }
    # IPv6 zonder [] -> 32 hexcijfers, of "" als het geen geldig adres is.
    function ipv6(a,    dub, links, rechts, nl, nr, i, uit, L, R) {
      if (a == "" || a !~ "^[0-9a-f:]+$") return ""
      dub = index(a, "::")
      if (dub) {
        if (index(substr(a, dub + 2), "::")) return ""
        links = substr(a, 1, dub - 1); rechts = substr(a, dub + 2)
        nl = (links == "") ? 0 : split(links, L, ":")
        nr = (rechts == "") ? 0 : split(rechts, R, ":")
        if (nl + nr > 7) return ""
      } else {
        nl = split(a, L, ":"); nr = 0
        if (nl != 8) return ""
      }
      uit = ""
      for (i = 1; i <= nl; i++) { if (!groep(L[i])) return ""; uit = uit vier(L[i]) }
      if (dub) for (i = nl + nr; i < 8; i++) uit = uit "0000"
      for (i = 1; i <= nr; i++) { if (!groep(R[i])) return ""; uit = uit vier(R[i]) }
      return uit
    }
    function ipv4(h,    n, d, i) {
      n = split(h, d, ".")
      if (n != 4) return 0
      for (i = 1; i <= 4; i++) if (d[i] !~ "^(0|[1-9][0-9]?[0-9]?)$" || d[i] + 0 > 255) return 0
      return 1
    }
    BEGIN {
      s = ENVIRON["HUB_URL"]
      if (length(s) > 2048) fout("langer dan 2048 tekens")
      if (s !~ "^[][A-Za-z0-9.:/-]+$") fout("tekens buiten http(s)://host[:poort] (spatie, pad, query, fragment, inloggegevens of niet-ASCII)")
      if (substr(s, 1, 7) == "http://") { schema = "http"; rest = substr(s, 8) }
      else if (substr(s, 1, 8) == "https://") { schema = "https"; rest = substr(s, 9) }
      else fout("schema moet http:// of https:// zijn")
      if (substr(rest, length(rest)) == "/") rest = substr(rest, 1, length(rest) - 1)
      if (index(rest, "/")) fout("geen pad toegestaan")
      if (substr(rest, 1, 1) == "[") {
        e = index(rest, "]")
        if (!e) fout("IPv6-adres zonder ]")
        host = substr(rest, 1, e); na = substr(rest, e + 1)
      } else {
        e = index(rest, ":")
        if (e) { host = substr(rest, 1, e - 1); na = substr(rest, e) } else { host = rest; na = "" }
        if (index(host, "[") || index(host, "]")) fout("IPv6-adres alleen tussen []")
      }
      if (na != "") {
        if (na !~ "^:[1-9][0-9]*$" || length(na) > 6 || substr(na, 2) + 0 > 65535) fout("poort niet 1-65535 (zonder voorloopnul)")
      }
      if (host == "") fout("geen host")
      h = tolower(host)
      if (substr(h, 1, 1) == "[") {
        x = ipv6(substr(h, 2, length(h) - 2))
        if (x == "") fout("geen geldig IPv6-adres (zonder zone en zonder IPv4-notatie)")
        if (x ~ "^0000000000000000000000000000000[01]$") fout("loopback of geen adres")
        if (substr(x, 1, 24) == "00000000000000000000ffff") fout("IPv4-gemapt adres")
        if (substr(x, 1, 3) ~ "^fe[89ab]$") fout("link-local adres")
      } else {
        kaal = h
        if (substr(kaal, length(kaal)) == ".") kaal = substr(kaal, 1, length(kaal) - 1)
        if (kaal == "" || length(kaal) > 253) fout("geen geldige hostnaam")
        n = split(kaal, lab, ".")
        for (i = 1; i <= n; i++) if (length(lab[i]) > 63 || lab[i] !~ "^[a-z0-9]([a-z0-9-]*[a-z0-9])?$") fout("geen geldige hostnaam")
        if (lab[n] ~ "^[0-9]+$" || lab[n] ~ "^0x[0-9a-f]*$") {
          if (!ipv4(kaal)) fout("IPv4-adres niet in de gewone vorm a.b.c.d")
          if (lab[1] == "127" || lab[1] == "0" || (lab[1] == "169" && lab[2] == "254")) fout("loopback, geen adres of link-local")
        } else if (kaal == "localhost" || kaal ~ "[.]localhost$") fout("loopback (localhost)")
      }
      print "ok " schema "://" h na
    }')" || uitkomst="fout de controle zelf faalde"
  case "$uitkomst" in
    "ok "*) json="{\"hub_url\":\"${uitkomst#ok }\"}" ;;
    *) echo "15-shc-config: HUB_URL genegeerd (${uitkomst#fout }); de link 'Terug naar de Hub' blijft verborgen." >&2 ;;
  esac
fi

mkdir -p "$dir"
printf '%s\n' "$json" >"$dir/config.json.tmp"
mv -f "$dir/config.json.tmp" "$dir/config.json"
