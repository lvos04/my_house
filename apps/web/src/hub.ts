// "Terug naar de Hub" (O-11; docs/ontwerp/PEIL_MIGRATIE.md §9.1, ADR-0008 punt 3). Het adres komt alleen
// uit de configuratie van de app: de webserver biedt de optionele omgevingsvariabele HUB_URL aan als
// /_shc/config.json ({"hub_url": "…"}). Nooit uit de URL, document.referrer of invoer van de gebruiker.
// Onbekend of ongeldig = geen link (geen grijze knop, geen plaatshouder).
import { useEffect, useState } from 'react';

export const HUB_CONFIG_URL = '/_shc/config.json';

/** IPv6 zonder [] als 32 hexcijfers, of null als het geen geldig adres is (geen zone, geen IPv4-notatie). */
function ipv6Hex(a: string): string | null {
  if (!a || !/^[0-9a-f:]+$/.test(a)) return null;
  const groep = /^[0-9a-f]{1,4}$/;
  const dub = a.indexOf('::');
  let links: string[];
  let rechts: string[] = [];
  if (dub >= 0) {
    if (a.indexOf('::', dub + 2) >= 0) return null;
    links = a.slice(0, dub) ? a.slice(0, dub).split(':') : [];
    rechts = a.slice(dub + 2) ? a.slice(dub + 2).split(':') : [];
    if (links.length + rechts.length > 7) return null;
  } else {
    links = a.split(':');
    if (links.length !== 8) return null;
  }
  if (![...links, ...rechts].every((g) => groep.test(g))) return null;
  const vul = dub >= 0 ? Array(8 - links.length - rechts.length).fill('0000') : [];
  return [...links, ...vul, ...rechts].map((g) => g.padStart(4, '0')).join('');
}

/** Canonieke IPv4-vorm a.b.c.d: vier decimale delen 0–255 zonder voorloopnul. */
function isGewoonIpv4(h: string): boolean {
  const d = h.split('.');
  return d.length === 4 && d.every((x) => /^(0|[1-9][0-9]{0,2})$/.test(x) && Number(x) <= 255);
}

/** Waarom de host niet mag, of null als hij mag (contract 1.3 §3.2; host al in kleine letters). */
function hostFout(h: string): string | null {
  if (h.startsWith('[')) {
    const x = ipv6Hex(h.slice(1, -1));
    if (!x) return 'geen geldig IPv6-adres';
    if (/^0{31}[01]$/.test(x)) return 'loopback of geen adres';
    if (x.startsWith('00000000000000000000ffff')) return 'IPv4-gemapt adres';
    if (/^fe[89ab]/.test(x)) return 'link-local';
    return null;
  }
  const kaal = h.endsWith('.') ? h.slice(0, -1) : h; // één punt aan het eind telt niet mee (localhost. = localhost)
  if (!kaal || kaal.length > 253) return 'geen geldige hostnaam';
  const labels = kaal.split('.');
  if (!labels.every((l) => l.length <= 63 && /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/.test(l))) return 'geen geldige hostnaam';
  const laatste = labels[labels.length - 1];
  // Eindigt de host op een getal (ook 0x…), dan leest de browser hem als IPv4-adres (127.1, 2130706433, 010.0.0.1).
  if (/^[0-9]+$/.test(laatste) || /^0x[0-9a-f]*$/.test(laatste)) {
    if (!isGewoonIpv4(kaal)) return 'IPv4-adres niet in de gewone vorm a.b.c.d';
    const [a, b] = labels;
    if (a === '127' || a === '0' || (a === '169' && b === '254')) return 'loopback, geen adres of link-local';
    return null;
  }
  if (kaal === 'localhost' || kaal.endsWith('.localhost')) return 'loopback';
  return null;
}

/**
 * Het Hub-adres als het voldoet aan de adresregels van contract 1.3 §3.2 (dezelfde regels en gevallen als het
 * startscript apps/web/docker/15-shc-config.sh, gedeeld via infra/tests/hub_url_gevallen.json):
 * http(s)://<host>[:<poort>] zonder pad, query, fragment of inloggegevens; IPv4 alleen als a.b.c.d, IPv6 alleen tussen
 * [] (zonder zone of IPv4-notatie), één punt aan het eind van een naam telt niet mee; geen loopback, link-local,
 * 0.0.0.0/8, :: of IPv4-gemapt adres. Eén afsluitende "/" mag. Geeft de origin terug, anders null.
 * my_house maakt zelf geen verbinding met dit adres: het is alleen het doel van een link (ADR-0012).
 */
export function geldigHubAdres(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const s = raw; // niet trimmen: een spatie maakt het adres ongeldig, net als in het startscript
  if (!s || s.length > 2048 || !/^[A-Za-z0-9.:/[\]-]+$/.test(s)) return null;
  const m = /^(https?):\/\/(\[[^\]]*\]|[^:/[\]]+)(?::([1-9][0-9]{0,4}))?\/?$/.exec(s);
  if (!m || (m[3] !== undefined && Number(m[3]) > 65535)) return null;
  const host = m[2].toLowerCase();
  if (hostFout(host)) return null;
  try {
    return new URL(`${m[1]}://${host}${m[3] ? `:${m[3]}` : ''}`).origin;
  } catch {
    return null;
  }
}

let cached: Promise<string | null> | null = null;

/** Leest /_shc/config.json één keer per paginalading. Elke fout (geen bestand, geen JSON) = null. */
export function laadHubAdres(): Promise<string | null> {
  if (!cached) {
    cached = (async () => {
      try {
        const res = await fetch(HUB_CONFIG_URL, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
        if (!res.ok || !(res.headers.get('content-type') ?? '').includes('application/json')) return null;
        const body: unknown = await res.json();
        if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
        return geldigHubAdres((body as Record<string, unknown>).hub_url);
      } catch {
        return null;
      }
    })();
  }
  return cached;
}

/** Alleen voor tests: vergeet het geladen adres. */
export function vergeetHubAdres(): void {
  cached = null;
}

export function useHubAdres(): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let actief = true;
    laadHubAdres().then((u) => {
      if (actief) setUrl(u);
    });
    return () => {
      actief = false;
    };
  }, []);
  return url;
}
